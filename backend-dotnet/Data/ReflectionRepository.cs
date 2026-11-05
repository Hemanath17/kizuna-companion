using Npgsql;

namespace backend_dotnet.Data;

// .NET owns reflection acceptance (computing the window, the idempotent
// insert, enqueuing the job), the same ownership shape as
// ChatRepository/JournalRepository's acceptance step. Python's worker owns
// generation and validation (agent-python/worker.py's handle_reflection_generate).
public sealed class ReflectionRepository(NpgsqlDataSource dataSource) : IReflectionRepository
{
    private const string SelectColumns =
        "id, period_start, period_end, body, status, rejection_reason, created_at, generated_at";

    public async Task<ReflectionRecord> CreateOrGetAsync(
        Guid userId, DateTimeOffset periodStart, DateTimeOffset periodEnd, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        var existing = await FindExistingAsync(connection, userId, periodStart, periodEnd, cancellationToken);
        if (existing is not null)
        {
            return existing;
        }

        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        var reflectionId = Guid.NewGuid();
        await using var insert = connection.CreateCommand();
        insert.CommandText = $"""
            insert into reflections (id, user_id, period_start, period_end, status)
            values (@id, @userId, @periodStart, @periodEnd, 'pending')
            on conflict (user_id, period_start, period_end) do nothing
            returning {SelectColumns}
            """;
        insert.Parameters.AddWithValue("id", reflectionId);
        insert.Parameters.AddWithValue("userId", userId);
        insert.Parameters.AddWithValue("periodStart", periodStart);
        insert.Parameters.AddWithValue("periodEnd", periodEnd);

        ReflectionRecord? created = null;
        await using (var reader = await insert.ExecuteReaderAsync(cancellationToken))
        {
            if (await reader.ReadAsync(cancellationToken))
            {
                created = ReadRecord(reader);
            }
        }

        if (created is null)
        {
            // Lost a race against a concurrent duplicate request that
            // committed first -- same recovery shape as Chat/JournalRepository.
            await transaction.RollbackAsync(cancellationToken);
            return await FindExistingAsync(connection, userId, periodStart, periodEnd, cancellationToken)
                ?? throw new InvalidOperationException(
                    "Insert hit a unique-constraint conflict but no existing row was found after rollback.");
        }

        // outbox.turn_id reused as a generic "subject id" (see
        // JournalRepository) -- it holds the reflection's own id.
        await using var insertOutbox = connection.CreateCommand();
        insertOutbox.CommandText = """
            insert into outbox (id, turn_id, user_id, job_type, status)
            values (@id, @reflectionId, @userId, 'reflection_generate', 'pending')
            """;
        insertOutbox.Parameters.AddWithValue("id", Guid.NewGuid());
        insertOutbox.Parameters.AddWithValue("reflectionId", reflectionId);
        insertOutbox.Parameters.AddWithValue("userId", userId);
        await insertOutbox.ExecuteNonQueryAsync(cancellationToken);

        await transaction.CommitAsync(cancellationToken);
        return created;
    }

    public async Task<IReadOnlyList<ReflectionRecord>> ListAsync(Guid userId, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = $"""
            select {SelectColumns}
            from reflections
            where user_id = @userId and status <> 'rejected'
            order by created_at desc
            """;
        command.Parameters.AddWithValue("userId", userId);

        var results = new List<ReflectionRecord>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(ReadRecord(reader));
        }
        return results;
    }

    public async Task<ReflectionRecord?> GetAsync(Guid userId, Guid id, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = $"""
            select {SelectColumns}
            from reflections
            where id = @id and user_id = @userId and status <> 'rejected'
            """;
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("userId", userId);
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    public async Task<bool> DeleteAsync(Guid userId, Guid id, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        // No status filter here: deleting a rejected reflection the user
        // never saw is still a legitimate cleanup operation.
        command.CommandText = "delete from reflections where id = @id and user_id = @userId";
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("userId", userId);
        return await command.ExecuteNonQueryAsync(cancellationToken) > 0;
    }

    public async Task<bool> AddNoteAsync(Guid userId, Guid reflectionId, string body, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        await using var ownershipCheck = connection.CreateCommand();
        ownershipCheck.CommandText =
            "select 1 from reflections where id = @id and user_id = @userId and status <> 'rejected'";
        ownershipCheck.Parameters.AddWithValue("id", reflectionId);
        ownershipCheck.Parameters.AddWithValue("userId", userId);
        if (await ownershipCheck.ExecuteScalarAsync(cancellationToken) is null)
        {
            return false;
        }

        await using var insert = connection.CreateCommand();
        insert.CommandText = """
            insert into reflection_notes (id, reflection_id, user_id, body)
            values (@id, @reflectionId, @userId, @body)
            """;
        insert.Parameters.AddWithValue("id", Guid.NewGuid());
        insert.Parameters.AddWithValue("reflectionId", reflectionId);
        insert.Parameters.AddWithValue("userId", userId);
        insert.Parameters.AddWithValue("body", body);
        await insert.ExecuteNonQueryAsync(cancellationToken);
        return true;
    }

    public async Task<IReadOnlyList<ReflectionNoteRecord>?> ListNotesAsync(
        Guid userId, Guid reflectionId, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);

        await using var ownershipCheck = connection.CreateCommand();
        ownershipCheck.CommandText =
            "select 1 from reflections where id = @id and user_id = @userId and status <> 'rejected'";
        ownershipCheck.Parameters.AddWithValue("id", reflectionId);
        ownershipCheck.Parameters.AddWithValue("userId", userId);
        if (await ownershipCheck.ExecuteScalarAsync(cancellationToken) is null)
        {
            return null;
        }

        await using var command = connection.CreateCommand();
        command.CommandText = "select id, body, created_at from reflection_notes where reflection_id = @id order by created_at";
        command.Parameters.AddWithValue("id", reflectionId);
        var results = new List<ReflectionNoteRecord>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(new ReflectionNoteRecord(reader.GetGuid(0), reader.GetString(1), reader.GetFieldValue<DateTimeOffset>(2)));
        }
        return results;
    }

    private static async Task<ReflectionRecord?> FindExistingAsync(
        NpgsqlConnection connection, Guid userId, DateTimeOffset periodStart, DateTimeOffset periodEnd,
        CancellationToken cancellationToken)
    {
        await using var select = connection.CreateCommand();
        select.CommandText = $"""
            select {SelectColumns}
            from reflections
            where user_id = @userId and period_start = @periodStart and period_end = @periodEnd
            """;
        select.Parameters.AddWithValue("userId", userId);
        select.Parameters.AddWithValue("periodStart", periodStart);
        select.Parameters.AddWithValue("periodEnd", periodEnd);
        await using var reader = await select.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    private static ReflectionRecord ReadRecord(NpgsqlDataReader reader) => new(
        reader.GetGuid(0),
        reader.GetFieldValue<DateTimeOffset>(1),
        reader.GetFieldValue<DateTimeOffset>(2),
        reader.IsDBNull(3) ? null : reader.GetString(3),
        reader.GetString(4),
        reader.IsDBNull(5) ? null : reader.GetString(5),
        reader.GetFieldValue<DateTimeOffset>(6),
        reader.IsDBNull(7) ? null : reader.GetFieldValue<DateTimeOffset>(7));
}
