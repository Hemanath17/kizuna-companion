using Npgsql;

namespace backend_dotnet.Tests;

// db/0001_schema_v2.sql claims a client can never set its own is_owner/plan,
// enforced by revoking table-level UPDATE and granting it back only on the
// other columns. That claim was flagged as unverified. This connects as the
// real 'authenticated' role (what PostgREST runs as for any Supabase client,
// including one that bypasses .NET/Python entirely) impersonating a specific
// user via the same request.jwt.claim.sub GUC auth.uid() reads, and sends the
// exact escalation a malicious client would send against its own row.
public sealed class PrivilegeEscalationTests
{
    [Fact]
    public async Task AuthenticatedRole_CannotSetIsOwnerOrPlan_OnOwnRow()
    {
        var databaseName = $"kizuna_privesc_test_{Guid.NewGuid():N}";
        await using var testDatabase = await TestDatabase.CreateAsync(databaseName);

        var userId = Guid.NewGuid();
        await testDatabase.SeedAuthUserAsync(userId);
        await testDatabase.SeedProfileAsync(userId, "victim@example.com");

        await using var attacker = new NpgsqlConnection(testDatabase.AdoConnectionString);
        await attacker.OpenAsync();
        await ExecuteAsync(attacker, "set role authenticated");
        await ExecuteAsync(attacker, $"select set_config('request.jwt.claim.sub', '{userId}', false)");

        PostgresException? rejection = null;
        try
        {
            await using var attack = attacker.CreateCommand();
            attack.CommandText = "update profiles set is_owner = true, plan = 'pro' where id = @id";
            attack.Parameters.AddWithValue("id", userId);
            await attack.ExecuteNonQueryAsync();
        }
        catch (PostgresException ex)
        {
            rejection = ex;
        }

        // Control: the same role, same impersonated user, updating a column
        // it IS granted, must still work — proves a failure above is a
        // targeted block on is_owner/plan, not every grant being broken.
        await ExecuteAsync(attacker, "update profiles set name = 'victim' where id = '" + userId + "'");

        await using var admin = new NpgsqlConnection(testDatabase.AdoConnectionString);
        await admin.OpenAsync();
        await using var check = admin.CreateCommand();
        check.CommandText = "select is_owner, plan, name from profiles where id = @id";
        check.Parameters.AddWithValue("id", userId);
        await using var reader = await check.ExecuteReaderAsync();
        await reader.ReadAsync();

        Assert.False(reader.GetBoolean(0), $"is_owner must still be false; rejection={rejection?.SqlState}");
        Assert.Equal("free", reader.GetString(1));
        Assert.Equal("victim", reader.GetString(2));
    }

    private static async Task ExecuteAsync(NpgsqlConnection connection, string sql)
    {
        await using var command = connection.CreateCommand();
        command.CommandText = sql;
        await command.ExecuteNonQueryAsync();
    }
}
