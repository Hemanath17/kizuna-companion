using System.Net.Http.Headers;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace backend_dotnet.Tests;

// The one test in this file exercises the real ProfileRepository against a
// disposable local Postgres database, instead of the RecordingProfileRepository
// the rest of AuthTests use, to prove the actual INSERT ... ON CONFLICT happens.
public sealed class ProfileUpsertTests
{
    [Fact]
    public async Task Me_CreatesProfileRow_OnFirstAuthenticatedCall()
    {
        var databaseName = $"kizuna_backend_test_{Guid.NewGuid():N}";
        await using var testDatabase = await TestDatabase.CreateAsync(databaseName);

        using var jwt = new TestJwt();
        using var factory = new ProfileUpsertApiFactory(testDatabase.ConnectionUrl, jwt.JwksJson);

        const string userId = "33333333-3333-3333-3333-333333333333";
        const string email = "first-call@example.com";
        await testDatabase.SeedAuthUserAsync(Guid.Parse(userId));
        var token = jwt.CreateToken(subject: userId, email: email);

        var client = factory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.GetAsync("/me");
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        await using var connection = new NpgsqlConnection(testDatabase.AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select email, is_owner, plan from profiles where id = @id";
        command.Parameters.AddWithValue("id", Guid.Parse(userId));
        await using var reader = await command.ExecuteReaderAsync();

        Assert.True(await reader.ReadAsync(), "expected a profiles row to have been created");
        Assert.Equal(email, reader.GetString(0));
        Assert.False(reader.GetBoolean(1));
        Assert.Equal("free", reader.GetString(2));
    }

    private sealed class ProfileUpsertApiFactory : Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactory<Program>
    {
        private readonly string _jwksJson;

        public ProfileUpsertApiFactory(string databaseUrl, string jwksJson)
        {
            _jwksJson = jwksJson;
            Environment.SetEnvironmentVariable("SUPABASE_URL", "https://test.supabase.co");
            Environment.SetEnvironmentVariable("SUPABASE_JWKS_URL", "https://test.supabase.co/auth/v1/.well-known/jwks.json");
            Environment.SetEnvironmentVariable("SUPABASE_ISSUER", TestJwt.Issuer);
            Environment.SetEnvironmentVariable("SUPABASE_AUDIENCE", TestJwt.Audience);
            Environment.SetEnvironmentVariable("DATABASE_URL", databaseUrl);
            Environment.SetEnvironmentVariable("INTERNAL_SIGNING_KEY", InternalTokenTestDefaults.SigningKey);
            Environment.SetEnvironmentVariable("INTERNAL_TOKEN_ISSUER", InternalTokenTestDefaults.Issuer);
            Environment.SetEnvironmentVariable("INTERNAL_TOKEN_AUDIENCE", InternalTokenTestDefaults.Audience);
            Environment.SetEnvironmentVariable("AGENT_PYTHON_URL", "http://127.0.0.1:1");
        }

        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton<HttpMessageHandler>(new StubHttpMessageHandler(_jwksJson));
            });
        }
    }
}
