using Npgsql;

namespace backend_dotnet.Data;

// is_owner and plan are deliberately not in this statement: they take their
// column defaults, and the migration revokes client write access to them
// anyway (see db/0001_schema_v2.sql).
public sealed class ProfileRepository(NpgsqlDataSource dataSource) : IProfileRepository
{
    public async Task EnsureProfileAsync(Guid userId, string? email, CancellationToken cancellationToken)
    {
        await using var connection = await dataSource.OpenConnectionAsync(cancellationToken);
        await using var command = connection.CreateCommand();
        command.CommandText = """
            insert into profiles (id, email)
            values (@id, @email)
            on conflict (id) do nothing
            """;
        command.Parameters.AddWithValue("id", userId);
        command.Parameters.AddWithValue("email", (object?)email ?? DBNull.Value);
        await command.ExecuteNonQueryAsync(cancellationToken);
    }
}
