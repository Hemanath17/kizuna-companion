using Npgsql;

namespace backend_dotnet.Data;

// .NET owns turn ACCEPTANCE (PLAN.md section 16 item 1): the
// UNIQUE(user_id, idempotency_key) constraint is the real dedup guarantee,
// not a check-then-insert. This connection does not scope identity through
// RLS the way the Python agent's does (known gap, same category as the
// messages/outbox gap already documented in db/README.md); ownership is
// instead checked explicitly in application code below.
public sealed class ChatRepository(NpgsqlDataSource dataSource) : IChatRepository
{
    public async Task<Guid?> EnsureConversationAsync(Guid userId, Guid? conversationId, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        if (conversationId is null)
        {
            var newId = Guid.NewGuid();
            await using var insert = connection.CreateCommand();
            insert.CommandText = "insert into conversations (id, user_id) values (@id, @userId)";
            insert.Parameters.AddWithValue("id", newId);
            insert.Parameters.AddWithValue("userId", userId);
            await insert.ExecuteNonQueryAsync(cancellationToken);
            return newId;
        }

        await using var select = connection.CreateCommand();
        select.CommandText = "select 1 from conversations where id = @id and user_id = @userId";
        select.Parameters.AddWithValue("id", conversationId.Value);
        select.Parameters.AddWithValue("userId", userId);
        var found = await select.ExecuteScalarAsync(cancellationToken);
        return found is null ? null : conversationId;
    }

    public async Task<AcceptedTurn> AcceptUserMessageAsync(
        Guid userId, Guid conversationId, string text, string idempotencyKey, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        // Sequencing races across concurrent turns in the same conversation
        // are a known gap (PLAN.md's per-conversation serialization is
        // Phase 5 hardening); correct for a single turn, which is this step's
        // scope.
        await using var sequenceCommand = connection.CreateCommand();
        sequenceCommand.CommandText = "select coalesce(max(sequence_number), -1) + 1 from messages where conversation_id = @conversationId";
        sequenceCommand.Parameters.AddWithValue("conversationId", conversationId);
        var sequenceNumber = (int)(await sequenceCommand.ExecuteScalarAsync(cancellationToken))!;

        var turnId = Guid.NewGuid();

        await using var insert = connection.CreateCommand();
        insert.CommandText = """
            insert into messages (id, user_id, conversation_id, turn_id, idempotency_key, sequence_number, role, content, status)
            values (@id, @userId, @conversationId, @turnId, @idempotencyKey, @sequenceNumber, 'user', @text, 'accepted')
            on conflict (user_id, idempotency_key) do nothing
            returning turn_id, sequence_number, status
            """;
        insert.Parameters.AddWithValue("id", Guid.NewGuid());
        insert.Parameters.AddWithValue("userId", userId);
        insert.Parameters.AddWithValue("conversationId", conversationId);
        insert.Parameters.AddWithValue("turnId", turnId);
        insert.Parameters.AddWithValue("idempotencyKey", idempotencyKey);
        insert.Parameters.AddWithValue("sequenceNumber", sequenceNumber);
        insert.Parameters.AddWithValue("text", text);

        await using var reader = await insert.ExecuteReaderAsync(cancellationToken);
        if (await reader.ReadAsync(cancellationToken))
        {
            var result = new AcceptedTurn(reader.GetGuid(0), reader.GetInt32(1), reader.GetString(2), IsNew: true);
            await reader.DisposeAsync();
            await transaction.CommitAsync(cancellationToken);
            return result;
        }
        await reader.DisposeAsync();

        // Duplicate: a row for this (user_id, idempotency_key) already
        // existed, so the insert above was a no-op. Fetch it instead of
        // creating a second turn (PLAN.md section 16 item 3: reject-as-in-progress).
        await using var existing = connection.CreateCommand();
        existing.CommandText = "select turn_id, sequence_number, status from messages where user_id = @userId and idempotency_key = @idempotencyKey";
        existing.Parameters.AddWithValue("userId", userId);
        existing.Parameters.AddWithValue("idempotencyKey", idempotencyKey);
        await using var existingReader = await existing.ExecuteReaderAsync(cancellationToken);
        await existingReader.ReadAsync(cancellationToken);
        var duplicate = new AcceptedTurn(
            existingReader.GetGuid(0), existingReader.GetInt32(1), existingReader.GetString(2), IsNew: false);
        await existingReader.DisposeAsync();
        await transaction.CommitAsync(cancellationToken);
        return duplicate;
    }
}
