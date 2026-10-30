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
    public async Task<AcceptedTurn?> AcceptUserMessageAsync(
        Guid userId, Guid? requestedConversationId, string text, string idempotencyKey, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        if (requestedConversationId is not null)
        {
            await using var ownershipCheck = connection.CreateCommand();
            ownershipCheck.CommandText = "select 1 from conversations where id = @id and user_id = @userId";
            ownershipCheck.Parameters.AddWithValue("id", requestedConversationId.Value);
            ownershipCheck.Parameters.AddWithValue("userId", userId);
            // Covers both "doesn't exist" and "belongs to another user" the
            // same way, so a cross-user probe can't distinguish the two.
            if (await ownershipCheck.ExecuteScalarAsync(cancellationToken) is null)
            {
                return null;
            }
        }

        // Check for a duplicate BEFORE creating anything: this is what stops
        // a blind retry (no conversation_id yet, because the first response
        // was dropped before the client learned it) from leaking an empty
        // conversation row on every replay.
        var existing = await FindExistingTurnAsync(connection, userId, idempotencyKey, cancellationToken);
        if (existing is not null)
        {
            return existing;
        }

        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        var conversationId = requestedConversationId ?? Guid.NewGuid();
        if (requestedConversationId is null)
        {
            await using var insertConversation = connection.CreateCommand();
            insertConversation.CommandText = "insert into conversations (id, user_id) values (@id, @userId)";
            insertConversation.Parameters.AddWithValue("id", conversationId);
            insertConversation.Parameters.AddWithValue("userId", userId);
            await insertConversation.ExecuteNonQueryAsync(cancellationToken);
        }

        // Sequencing races across concurrent turns in the same conversation
        // are a known gap (PLAN.md's per-conversation serialization is
        // Phase 5 hardening); correct for a single turn, which is this step's
        // scope.
        await using var sequenceCommand = connection.CreateCommand();
        sequenceCommand.CommandText = "select coalesce(max(sequence_number), -1) + 1 from messages where conversation_id = @conversationId";
        sequenceCommand.Parameters.AddWithValue("conversationId", conversationId);
        var sequenceNumber = (int)(await sequenceCommand.ExecuteScalarAsync(cancellationToken))!;

        var turnId = Guid.NewGuid();

        await using var insertMessage = connection.CreateCommand();
        insertMessage.CommandText = """
            insert into messages (id, user_id, conversation_id, turn_id, idempotency_key, sequence_number, role, content, status)
            values (@id, @userId, @conversationId, @turnId, @idempotencyKey, @sequenceNumber, 'user', @text, 'accepted')
            on conflict (user_id, idempotency_key) do nothing
            returning turn_id, sequence_number, status
            """;
        insertMessage.Parameters.AddWithValue("id", Guid.NewGuid());
        insertMessage.Parameters.AddWithValue("userId", userId);
        insertMessage.Parameters.AddWithValue("conversationId", conversationId);
        insertMessage.Parameters.AddWithValue("turnId", turnId);
        insertMessage.Parameters.AddWithValue("idempotencyKey", idempotencyKey);
        insertMessage.Parameters.AddWithValue("sequenceNumber", sequenceNumber);
        insertMessage.Parameters.AddWithValue("text", text);

        await using var reader = await insertMessage.ExecuteReaderAsync(cancellationToken);
        var wonRace = await reader.ReadAsync(cancellationToken);
        var won = wonRace
            ? new AcceptedTurn(conversationId, reader.GetGuid(0), reader.GetInt32(1), reader.GetString(2), IsNew: true)
            : null;
        await reader.DisposeAsync();

        if (won is not null)
        {
            await transaction.CommitAsync(cancellationToken);
            return won;
        }

        // Lost a race against a concurrent duplicate request that committed
        // first: roll back so the conversation row created above (if any)
        // never survives, then re-read the winner's row on a clean
        // transaction -- same "create nothing on a duplicate" guarantee as
        // the pre-existing-row path above, just for the concurrent case.
        await transaction.RollbackAsync(cancellationToken);
        return await FindExistingTurnAsync(connection, userId, idempotencyKey, cancellationToken)
            ?? throw new InvalidOperationException(
                "Insert hit a unique-constraint conflict but no existing row was found after rollback.");
    }

    private static async Task<AcceptedTurn?> FindExistingTurnAsync(
        NpgsqlConnection connection, Guid userId, string idempotencyKey, CancellationToken cancellationToken)
    {
        await using var select = connection.CreateCommand();
        select.CommandText = """
            select conversation_id, turn_id, sequence_number, status
            from messages
            where user_id = @userId and idempotency_key = @idempotencyKey
            """;
        select.Parameters.AddWithValue("userId", userId);
        select.Parameters.AddWithValue("idempotencyKey", idempotencyKey);
        await using var reader = await select.ExecuteReaderAsync(cancellationToken);
        if (!await reader.ReadAsync(cancellationToken))
        {
            return null;
        }
        return new AcceptedTurn(reader.GetGuid(0), reader.GetGuid(1), reader.GetInt32(2), reader.GetString(3), IsNew: false);
    }
}
