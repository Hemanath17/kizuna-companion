using System.Security.Cryptography;
using System.Text;
using Npgsql;

namespace backend_dotnet.Data;

// Mirrors agent-python/memory_store.py's ownership pattern: direct SQL with
// an explicit `where user_id = @userId`, not RLS -- the same documented gap
// as ChatRepository/ProfileRepository (see db/README.md).
public sealed class MemoryRepository(NpgsqlDataSource dataSource) : IMemoryRepository
{
    private const string SelectColumns = """
        id, memory_type, content, confidence, status, user_verified, is_sensitive,
        pending_confirmation, source_kind, superseded_by, created_at, updated_at
        """;

    public async Task<IReadOnlyList<MemoryRecord>> ListAsync(Guid userId, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = $"""
            select {SelectColumns}
            from memory
            where user_id = @userId and status <> 'deleted'
            order by created_at desc
            """;
        command.Parameters.AddWithValue("userId", userId);

        var results = new List<MemoryRecord>();
        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        while (await reader.ReadAsync(cancellationToken))
        {
            results.Add(ReadRecord(reader));
        }
        return results;
    }

    public async Task<MemoryRecord?> CorrectOrApproveAsync(
        Guid userId, Guid memoryId, string? content, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        // user_verified=true and pending_confirmation=false unconditionally:
        // a correction and a bare approval (content omitted) are both the
        // user confirming this fact, per PLAN.md section 6's "correct -> sets
        // user_verified" lifecycle step.
        //
        // Known gap: this does not recompute the embedding column when
        // content changes, unlike the memory agent's own UPDATE path
        // (policy.py), which inserts a fresh row with a freshly computed
        // embedding instead of mutating in place. .NET has no embedding
        // model access -- PLAN.md section 6 keeps embeddings behind
        // agent-python's provider abstraction only -- so a corrected
        // memory's embedding can go stale (still reflecting the old
        // content) until Phase 5 hardening adds a re-embed callback.
        command.CommandText = $"""
            update memory
            set content = coalesce(@content, content),
                user_verified = true,
                pending_confirmation = false,
                updated_at = now()
            where id = @id and user_id = @userId
            returning {SelectColumns}
            """;
        command.Parameters.AddWithValue("content", (object?)content ?? DBNull.Value);
        command.Parameters.AddWithValue("id", memoryId);
        command.Parameters.AddWithValue("userId", userId);

        await using var reader = await command.ExecuteReaderAsync(cancellationToken);
        return await reader.ReadAsync(cancellationToken) ? ReadRecord(reader) : null;
    }

    public async Task<bool> ForgetAsync(Guid userId, Guid memoryId, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var transaction = await connection.BeginTransactionAsync(cancellationToken);

        await using var fetch = connection.CreateCommand();
        fetch.CommandText = "select content from memory where id = @id and user_id = @userId and status <> 'deleted'";
        fetch.Parameters.AddWithValue("id", memoryId);
        fetch.Parameters.AddWithValue("userId", userId);
        var content = (string?)await fetch.ExecuteScalarAsync(cancellationToken);
        if (content is null)
        {
            return false;
        }

        // Mirrors agent-python/memory_store.py's tombstone_memory: same
        // content-hash normalization (so either side's check_tombstone call
        // recognizes a tombstone the other side wrote), and the embedding is
        // copied from the row being forgotten rather than recomputed, so
        // the forgotten fact's own embedding is what later blocks a
        // reworded resurrection.
        await using var tombstone = connection.CreateCommand();
        tombstone.CommandText = """
            insert into memory_tombstones (user_id, content_hash, embedding)
            select @userId, @contentHash, embedding from memory where id = @id
            on conflict (user_id, content_hash) do nothing
            """;
        tombstone.Parameters.AddWithValue("userId", userId);
        tombstone.Parameters.AddWithValue("contentHash", ContentHash(content));
        tombstone.Parameters.AddWithValue("id", memoryId);
        await tombstone.ExecuteNonQueryAsync(cancellationToken);

        await using var forget = connection.CreateCommand();
        forget.CommandText = """
            update memory set status = 'deleted', embedding = null, updated_at = now()
            where id = @id and user_id = @userId
            """;
        forget.Parameters.AddWithValue("id", memoryId);
        forget.Parameters.AddWithValue("userId", userId);
        await forget.ExecuteNonQueryAsync(cancellationToken);

        await transaction.CommitAsync(cancellationToken);
        return true;
    }

    private static MemoryRecord ReadRecord(NpgsqlDataReader reader) => new(
        reader.GetGuid(0),
        reader.GetString(1),
        reader.GetString(2),
        reader.IsDBNull(3) ? null : reader.GetDouble(3),
        reader.GetString(4),
        reader.GetBoolean(5),
        reader.GetBoolean(6),
        reader.GetBoolean(7),
        reader.GetString(8),
        reader.IsDBNull(9) ? null : reader.GetGuid(9),
        reader.GetFieldValue<DateTimeOffset>(10),
        reader.GetFieldValue<DateTimeOffset>(11));

    // Matches agent-python/memory_store.py's content_hash exactly: sha256 of
    // the content lowercased, trimmed, and collapsed to single spaces.
    private static string ContentHash(string content)
    {
        var normalized = string.Join(
            ' ', content.Trim().ToLowerInvariant().Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        var bytes = SHA256.HashData(Encoding.UTF8.GetBytes(normalized));
        return Convert.ToHexStringLower(bytes);
    }
}
