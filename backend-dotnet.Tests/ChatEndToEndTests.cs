using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace backend_dotnet.Tests;

// Integration tests against the real trust boundary: a real .NET app, a real
// spawned Python agent process, and a real local Postgres database shared by
// both -- as asked ("real local Postgres + both services"), not stand-ins.
public sealed class ChatEndToEndTests : IClassFixture<ChatE2EFixture>
{
    private readonly ChatE2EFixture _fixture;

    public ChatEndToEndTests(ChatE2EFixture fixture) => _fixture = fixture;

    private static StringContent ChatBody(string? conversationId, string text, string idempotencyKey)
    {
        var json = JsonSerializer.Serialize(new { conversation_id = conversationId, text, idempotency_key = idempotencyKey });
        return new StringContent(json, Encoding.UTF8, "application/json");
    }

    private async Task SeedUserAsync(Guid userId, string email)
    {
        await _fixture.Database.SeedAuthUserAsync(userId);
        await _fixture.Database.SeedProfileAsync(userId, email);
    }

    [Fact]
    public async Task FullTurn_CreatesConversation_StreamsEndToEnd_AndPersistsAtomically()
    {
        var userId = Guid.NewGuid();
        await SeedUserAsync(userId, "full-turn@example.com");
        var token = _fixture.Jwt.CreateToken(subject: userId.ToString(), email: "full-turn@example.com");

        var client = _fixture.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.PostAsync("/chat", ChatBody(null, "hello end to end", "e2e-turn-1"));
        var body = await response.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("event: token", body);
        Assert.Contains("event: done", body);

        await using var connection = new NpgsqlConnection(_fixture.Database.AdoConnectionString);
        await connection.OpenAsync();
        await using var findConversation = connection.CreateCommand();
        findConversation.CommandText = "select id from conversations where user_id = @userId";
        findConversation.Parameters.AddWithValue("userId", userId);
        var conversationId = (Guid)(await findConversation.ExecuteScalarAsync())!;

        var messages = await _fixture.Database.FetchMessagesAsync(conversationId);
        Assert.Equal(2, messages.Count);
        Assert.Contains(messages, m => m.Role == "user" && m.Status == "accepted");
        Assert.Contains(messages, m => m.Role == "assistant" && m.Status == "complete");

        await using var findTurn = connection.CreateCommand();
        findTurn.CommandText = "select turn_id from messages where conversation_id = @id and role = 'user'";
        findTurn.Parameters.AddWithValue("id", conversationId);
        var turnId = (Guid)(await findTurn.ExecuteScalarAsync())!;
        Assert.Equal(1, await _fixture.Database.CountOutboxRowsAsync(turnId));
    }

    [Fact]
    public async Task DuplicateIdempotencyKey_YieldsOneTurn_SecondCallReturnsExistingStatus()
    {
        var userId = Guid.NewGuid();
        await SeedUserAsync(userId, "dup@example.com");
        var conversationId = await _fixture.Database.SeedConversationAsync(userId);
        var token = _fixture.Jwt.CreateToken(subject: userId.ToString(), email: "dup@example.com");

        var client = _fixture.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var first = await client.PostAsync("/chat", ChatBody(conversationId.ToString(), "hello", "dup-key-1"));
        Assert.Equal(HttpStatusCode.OK, first.StatusCode);
        await first.Content.ReadAsStringAsync(); // drain fully so the first turn's commit has happened

        var second = await client.PostAsync("/chat", ChatBody(conversationId.ToString(), "hello", "dup-key-1"));
        Assert.Equal(HttpStatusCode.Conflict, second.StatusCode);
        var secondBody = JsonSerializer.Deserialize<JsonElement>(await second.Content.ReadAsStringAsync());
        Assert.Equal("accepted", secondBody.GetProperty("status").GetString());

        var messages = await _fixture.Database.FetchMessagesAsync(conversationId);
        Assert.Single(messages, m => m.Role == "user");
        Assert.Single(messages, m => m.Role == "assistant");

        var turnId = Guid.Parse(secondBody.GetProperty("turn_id").GetString()!);
        Assert.Equal(1, await _fixture.Database.CountOutboxRowsAsync(turnId));
    }

    [Fact]
    public async Task MismatchedInternalSigningKey_PythonRejects_DotNetSurfacesErrorEvent()
    {
        var userId = Guid.NewGuid();
        await SeedUserAsync(userId, "mismatch@example.com");
        var token = _fixture.Jwt.CreateToken(subject: userId.ToString(), email: "mismatch@example.com");

        // A second .NET instance pointed at the SAME running Python agent and
        // database, but with a signing key Python was never configured with --
        // a real signature mismatch, not a simulated one.
        using var mismatchedFactory = new MismatchedKeyApiFactory(_fixture.Database, _fixture.Agent, _fixture.Jwt);
        var client = mismatchedFactory.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.PostAsync("/chat", ChatBody(null, "hello", "mismatch-key-1"));
        var body = await response.Content.ReadAsStringAsync();

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Contains("event: error", body);

        await using var connection = new NpgsqlConnection(_fixture.Database.AdoConnectionString);
        await connection.OpenAsync();
        await using var findConversation = connection.CreateCommand();
        findConversation.CommandText = "select id from conversations where user_id = @userId";
        findConversation.Parameters.AddWithValue("userId", userId);
        var conversationId = (Guid)(await findConversation.ExecuteScalarAsync())!;

        var messages = await _fixture.Database.FetchMessagesAsync(conversationId);
        // The user message is durable (accepted before Python was ever
        // called); Python rejected the token before touching anything, so
        // there is no assistant row and its status is untouched.
        Assert.Single(messages);
        Assert.Equal(("user", "accepted"), (messages[0].Role, messages[0].Status));
    }

    [Fact]
    public async Task CrossUser_CannotWriteIntoAnotherUsersConversation()
    {
        var userA = Guid.NewGuid();
        var userB = Guid.NewGuid();
        await SeedUserAsync(userA, "user-a@example.com");
        await SeedUserAsync(userB, "user-b@example.com");
        var conversationOwnedByA = await _fixture.Database.SeedConversationAsync(userA);

        var tokenB = _fixture.Jwt.CreateToken(subject: userB.ToString(), email: "user-b@example.com");
        var client = _fixture.CreateClient();
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", tokenB);

        var response = await client.PostAsync(
            "/chat", ChatBody(conversationOwnedByA.ToString(), "trying to write into A's conversation", "cross-user-1"));

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
        Assert.Empty(await _fixture.Database.FetchMessagesAsync(conversationOwnedByA));
    }

    private sealed class MismatchedKeyApiFactory : WebApplicationFactory<Program>
    {
        private readonly TestJwt _jwt;

        public MismatchedKeyApiFactory(TestDatabase database, PythonAgentProcess agent, TestJwt jwt)
        {
            _jwt = jwt;
            Environment.SetEnvironmentVariable("SUPABASE_URL", "https://test.supabase.co");
            Environment.SetEnvironmentVariable("SUPABASE_JWKS_URL", "https://test.supabase.co/auth/v1/.well-known/jwks.json");
            Environment.SetEnvironmentVariable("SUPABASE_ISSUER", TestJwt.Issuer);
            Environment.SetEnvironmentVariable("SUPABASE_AUDIENCE", TestJwt.Audience);
            Environment.SetEnvironmentVariable("DATABASE_URL", database.ConnectionUrl);
            Environment.SetEnvironmentVariable("INTERNAL_SIGNING_KEY", "a-different-key-python-never-saw-at-all-32bytes");
            Environment.SetEnvironmentVariable("INTERNAL_TOKEN_ISSUER", InternalTokenTestDefaults.Issuer);
            Environment.SetEnvironmentVariable("INTERNAL_TOKEN_AUDIENCE", InternalTokenTestDefaults.Audience);
            Environment.SetEnvironmentVariable("AGENT_PYTHON_URL", agent.BaseUrl);
        }

        protected override void ConfigureWebHost(IWebHostBuilder builder)
        {
            builder.ConfigureServices(services =>
            {
                services.AddSingleton<HttpMessageHandler>(new StubHttpMessageHandler(_jwt.JwksJson));
            });
        }
    }
}
