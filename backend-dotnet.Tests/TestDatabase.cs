using Npgsql;

namespace backend_dotnet.Tests;

// A disposable local Postgres database for ProfileUpsertTests: creates a
// uniquely-named database, stubs the bits of Supabase's "auth" schema the
// real migration relies on (auth.users, auth.uid()), then applies the real
// migration file from /db so the test runs against the actual schema.
public sealed class TestDatabase : IAsyncDisposable
{
    private readonly string _databaseName;

    public string ConnectionUrl { get; }
    public string AdoConnectionString { get; }

    // Base URL the spawned Python agent connects with (kizuna_agent: a
    // login role that does NOT bypass RLS, same as agent-python's tests).
    public string AgentDatabaseUrl => $"postgresql://kizuna_agent@127.0.0.1:5432/{_databaseName}";

    // profiles.id has a real FK to auth.users(id): a genuine Supabase user
    // already exists there by the time they hold an access token, so tests
    // must seed that row too.
    public async Task SeedAuthUserAsync(Guid id)
    {
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "insert into auth.users (id) values (@id)";
        command.Parameters.AddWithValue("id", id);
        await command.ExecuteNonQueryAsync();
    }

    // Inserts the baseline row an escalation attempt would target, via the
    // admin (superuser, RLS-bypassing) connection, so the attempt itself is
    // the only thing exercising the authenticated role's real privileges.
    public async Task SeedProfileAsync(Guid id, string email)
    {
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "insert into profiles (id, email) values (@id, @email)";
        command.Parameters.AddWithValue("id", id);
        command.Parameters.AddWithValue("email", email);
        await command.ExecuteNonQueryAsync();
    }

    public async Task<Guid> SeedConversationAsync(Guid userId)
    {
        var conversationId = Guid.NewGuid();
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "insert into conversations (id, user_id) values (@id, @userId)";
        command.Parameters.AddWithValue("id", conversationId);
        command.Parameters.AddWithValue("userId", userId);
        await command.ExecuteNonQueryAsync();
        return conversationId;
    }

    public async Task<List<(string Role, string Status, string Content)>> FetchMessagesAsync(Guid conversationId)
    {
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select role, status, content from messages where conversation_id = @id order by sequence_number";
        command.Parameters.AddWithValue("id", conversationId);
        await using var reader = await command.ExecuteReaderAsync();
        var rows = new List<(string, string, string)>();
        while (await reader.ReadAsync())
        {
            rows.Add((reader.GetString(0), reader.GetString(1), reader.GetString(2)));
        }
        return rows;
    }

    public async Task<int> CountOutboxRowsAsync(Guid turnId)
    {
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select count(*) from outbox where turn_id = @turnId";
        command.Parameters.AddWithValue("turnId", turnId);
        return (int)(long)(await command.ExecuteScalarAsync())!;
    }

    public async Task<int> CountConversationsAsync(Guid userId)
    {
        await using var connection = new NpgsqlConnection(AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select count(*) from conversations where user_id = @userId";
        command.Parameters.AddWithValue("userId", userId);
        return (int)(long)(await command.ExecuteScalarAsync())!;
    }

    private TestDatabase(string databaseName, string connectionUrl, string adoConnectionString)
    {
        _databaseName = databaseName;
        ConnectionUrl = connectionUrl;
        AdoConnectionString = adoConnectionString;
    }

    public static async Task<TestDatabase> CreateAsync(string databaseName)
    {
        var user = Environment.UserName;
        var adminConnectionString = $"Host=localhost;Username={user};Database=postgres";

        await using (var admin = new NpgsqlConnection(adminConnectionString))
        {
            await admin.OpenAsync();
            await ExecuteAsync(admin, $"create database \"{databaseName}\"");
        }

        var dbConnectionString = $"Host=localhost;Username={user};Database={databaseName}";
        await using (var db = new NpgsqlConnection(dbConnectionString))
        {
            await db.OpenAsync();

            // Roles are cluster-wide, not per-database, so earlier test runs
            // may have already created these.
            await ExecuteAsync(db, """
                do $$
                begin
                    if not exists (select from pg_roles where rolname = 'authenticated') then
                        create role authenticated;
                    end if;
                    if not exists (select from pg_roles where rolname = 'anon') then
                        create role anon;
                    end if;
                    if not exists (select from pg_roles where rolname = 'kizuna_agent') then
                        -- noinherit: must explicitly `set role authenticated` to use
                        -- its grants, matching Supabase's authenticator/PostgREST
                        -- pattern. Login + no BYPASSRLS so RLS is enforced for real
                        -- when the spawned Python agent connects as this role.
                        create role kizuna_agent with login noinherit;
                    end if;
                    grant authenticated to kizuna_agent;
                end
                $$
                """);
            await ExecuteAsync(db, "create schema auth");
            await ExecuteAsync(db, "create table auth.users (id uuid primary key default gen_random_uuid())");
            // Mirrors Supabase's real auth.uid(): reads the request.jwt.claim.sub
            // GUC that PostgREST sets per-request, instead of a hardcoded null, so
            // tests can impersonate a specific authenticated user.
            await ExecuteAsync(db, """
                create function auth.uid() returns uuid language sql stable as $$
                    select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
                $$
                """);

            // Supabase provisions every real project with default privileges that
            // grant anon/authenticated ALL on tables created afterward; migrations
            // (like 0001_schema_v2.sql) assume that baseline already exists and only
            // narrow it from there. Replicate it here so the narrowing is tested
            // against the same starting point as production, not a blank slate.
            await ExecuteAsync(db, "alter default privileges in schema public grant all on tables to authenticated, anon");

            foreach (var migrationPath in FindMigrationFilePaths())
            {
                var migrationSql = await File.ReadAllTextAsync(migrationPath);
                await ExecuteAsync(db, migrationSql);
            }
        }

        var connectionUrl = $"postgres://{user}@localhost:5432/{databaseName}";
        return new TestDatabase(databaseName, connectionUrl, dbConnectionString);
    }

    private static async Task ExecuteAsync(NpgsqlConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }

    private static string[] FindMigrationFilePaths()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "PLAN.md")))
        {
            directory = directory.Parent;
        }

        if (directory is null)
        {
            throw new InvalidOperationException($"Could not locate repo root (PLAN.md) from {AppContext.BaseDirectory}");
        }

        return
        [
            Path.Combine(directory.FullName, "db", "0001_schema_v2.sql"),
            Path.Combine(directory.FullName, "db", "0002_safety.sql"),
        ];
    }

    public async ValueTask DisposeAsync()
    {
        var user = Environment.UserName;
        await using var admin = new NpgsqlConnection($"Host=localhost;Username={user};Database=postgres");
        await admin.OpenAsync();

        // Drop fails if the app's pooled connections are still open against
        // this database, so force them closed first.
        await using (var terminateCommand = admin.CreateCommand())
        {
            terminateCommand.CommandText = "select pg_terminate_backend(pid) from pg_stat_activity where datname = @name and pid <> pg_backend_pid()";
            terminateCommand.Parameters.AddWithValue("name", _databaseName);
            await terminateCommand.ExecuteNonQueryAsync();
        }

        await ExecuteAsync(admin, $"drop database if exists \"{_databaseName}\"");
    }
}
