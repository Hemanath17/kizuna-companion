using Npgsql;

namespace backend_dotnet.Data;

// .NET owns journal writes (frozen contract #1): this is raw user-authored
// content, unlike memory, which is derived state with its own lifecycle
// (Phase 4 decision 1 moved that to Python). Same app-level ownership-check
// convention as ChatRepository/the old MemoryRepository (documented gap:
// not RLS-scoped, checked in application code).
public sealed class JournalRepository(NpgsqlDataSource dataSource) : IJournalRepository
{
    private const string SelectColumns = "id, body, mood, created_at, updated_at";

    public async Task<JournalEntryRecord> CreateAsync(
        Guid userId, string body, string? mood, string idempotencyKey, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        var existing = await FindExistingAsync(connection, userId, idempotencyKey, cancellationToken);
        if (existing is not null)
        {
            return existing;
        }

        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        var entryId = Guid.NewGuid();
        await using var insertEntry = connection.CreateCommand();
        insertEntry.CommandText = $"""
            insert into journal_entries (id, user_id, body, mood, idempotency_key)
            values (@id, @userId, @body, @mood, @idempotencyKey)
            on conflict (user_id, idempotency_key) do nothing
            returning {SelectColumns}
            """;
        insertEntry.Parameters.AddWithValue("id", entryId);
        insertEntry.Parameters.AddWithValue("userId", userId);
        insertEntry.Parameters.AddWithValue("body", body);
        insertEntry.Parameters.AddWithValue("mood", (object?)mood ?? DBNull.Value);
        insertEntry.Parameters.AddWithValue("idempotencyKey", idempotencyKey);

        JournalEntryRecord? created = null;
        await using (var reader = await insertEntry.ExecuteReaderAsync(cancellationToken))
        {
            if (await reader.ReadAsync(cancellationToken))
            {
                created = ReadRecord(reader);
            }
        }

        if (created is null)
        {
            // Lost a race against a concurrent duplicate request that
            // committed first -- same recovery shape as ChatRepository's.
            await transaction.RollbackAsync(cancellationToken);
            return await FindExistingAsync(connection, userId, idempotencyKey, cancellationToken)
                ?? throw new InvalidOperationException(
                    "Insert hit a unique-constraint conflict but no existing row was found after rollback.");
        }

        // Enqueues extraction in the SAME transaction as the entry insert
        // (PLAN.md section 16 contract 2's atomicity discipline, applied in
        // .NET this time since a journal write has no Python round-trip to
        // finalize through first, unlike chat). outbox.turn_id is reused as
        // a generic "subject id" column here -- it holds the journal
        // entry's id, not a chat turn_id; the column carries no FK to
        // messages, so this is a safe repurposing, not a schema violation.
        await using var insertOutbox = connection.CreateCommand();
        insertOutbox.CommandText = """
            insert into outbox (id, turn_id, user_id, job_type, status)
            values (@id, @entryId, @userId, 'journal_memory_extraction', 'pending')
            """;
        insertOutbox.Parameters.AddWithValue("id", Guid.NewGuid());
        insertOutbox.Parameters.AddWithValue("entryId", entryId);
        insertOutbox.Parameters.AddWithValue("userId", userId);
        await insertOutbox.ExecuteNonQueryAsync(cancellationToken);

        await transaction.CommitAsync(cancellationToken);
        return created;
    }

    public async Task<IReadOnlyList<JournalEntryRecord>> ListAsync(
        Guid userId, int limit, int offset, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = $"""
            select {SelectColumns}
            from journal_entries
            where user_id = @userId
            order by created_at desc
            limit @limit offset @offset
            """;
        command.Parameters.AddWithValue("userId", userId);
        command.Parameters.AddWithValue("limit", limit);
        command.Parameters.AddWithValue("offset", offset);

        var results = new List<JournalEntryRecord>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(ReadRecord(reader));
        }
        return results;
    }

    public async Task<JournalEntryRecord?> GetAsync(Guid userId, Guid id, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = $"select {SelectColumns} from journal_entries where id = @id and user_id = @userId";
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("userId", userId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    public async Task<JournalEntryRecord?> UpdateAsync(
        Guid userId, Guid id, string body, string? mood, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        // shortcut: mood is always overwritten with whatever the request
        // carries (null if omitted), not merged field-by-field -- the
        // journal editor always resends the entry's current mood, so this
        // doesn't silently clear it in normal use. Upgrade to a tri-state
        // "unset vs explicit null" body if a PATCH-partial client appears.
        command.CommandText = $"""
            update journal_entries
            set body = @body, mood = @mood, updated_at = now()
            where id = @id and user_id = @userId
            returning {SelectColumns}
            """;
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("userId", userId);
        command.Parameters.AddWithValue("body", body);
        command.Parameters.AddWithValue("mood", (object?)mood ?? DBNull.Value);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    public async Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = "delete from journal_entries where id = @id and user_id = @userId";
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("userId", userId);
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    private static async Task<JournalEntryRecord?> FindExistingAsync(
        NpgsqlConnection connection, Guid userId, string idempotencyKey, CancellationToken cancellationToken)
    {
        await using var select = connection.CreateCommand();
        select.CommandText = $"""
            select {SelectColumns}
            from journal_entries
            where user_id = @userId and idempotency_key = @idempotencyKey
            """;
        select.Parameters.AddWithValue("userId", userId);
        select.Parameters.AddWithValue("idempotencyKey", idempotencyKey);
        await using var reader = await select.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    private static JournalEntryRecord ReadRecord(NpgsqlDataReader reader) => new(
        reader.GetGuid(0),
        reader.GetString(1),
        reader.IsDBNull(2) ? null : reader.GetString(2),
        reader.GetFieldValue<DateTimeOffset>(3),
        reader.GetFieldValue<DateTimeOffset>(4));
}
