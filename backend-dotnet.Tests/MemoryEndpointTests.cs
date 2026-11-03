using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

namespace backend_dotnet.Tests;

// Exercises the real MemoryRepository against a disposable local Postgres
// database (same pattern as ProfileUpsertTests), covering PLAN.md section 6
// / 3.4's correction, forget, and cross-user requirements end to end through
// the HTTP endpoints.
public sealed class MemoryEndpointTests
{
    [Fact]
    public async Task Get_ListsMemoriesWithProvenanceAndLabels_IncludingPending()
    {
        await using var fixture = await MemoryTestFixture.CreateAsync();
        var userId = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(userId);
        await fixture.Database.SeedProfileAsync(userId, "person@example.com");
        await fixture.Database.SeedMemoryAsync(userId, "works as a data engineer");
        await fixture.Database.SeedMemoryAsync(userId, "has a chronic illness", isSensitive: true, pendingConfirmation: true);

        var client = fixture.CreateAuthorizedClient(userId);
        var response = await client.GetAsync("/memory");
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(2, body.GetArrayLength());
        var contents = body.EnumerateArray().Select(e => e.GetProperty("content").GetString()).ToHashSet();
        Assert.Contains("works as a data engineer", contents);
        Assert.Contains("has a chronic illness", contents);

        var pending = body.EnumerateArray().Single(e => e.GetProperty("content").GetString() == "has a chronic illness");
        Assert.True(pending.GetProperty("pending_confirmation").GetBoolean());
        Assert.True(pending.GetProperty("is_sensitive").GetBoolean());
        Assert.Equal("stated", pending.GetProperty("source_kind").GetString());
    }

    [Fact]
    public async Task Patch_CorrectsContentAndSetsUserVerified()
    {
        await using var fixture = await MemoryTestFixture.CreateAsync();
        var userId = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(userId);
        await fixture.Database.SeedProfileAsync(userId, "person@example.com");
        var memoryId = await fixture.Database.SeedMemoryAsync(userId, "has a big interview Friday at Acme");

        var client = fixture.CreateAuthorizedClient(userId);
        var response = await client.PatchAsync(
            $"/memory/{memoryId}", JsonContent.Create(new { content = "has a big interview Thursday at Acme" }));
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("has a big interview Thursday at Acme", body.GetProperty("content").GetString());
        Assert.True(body.GetProperty("user_verified").GetBoolean());
    }

    [Fact]
    public async Task Patch_ApprovesPendingSensitiveMemory_WithoutChangingContent()
    {
        await using var fixture = await MemoryTestFixture.CreateAsync();
        var userId = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(userId);
        await fixture.Database.SeedProfileAsync(userId, "person@example.com");
        var memoryId = await fixture.Database.SeedMemoryAsync(
            userId, "has a chronic illness", isSensitive: true, pendingConfirmation: true);

        var client = fixture.CreateAuthorizedClient(userId);
        var response = await client.PatchAsync($"/memory/{memoryId}", JsonContent.Create(new { }));
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("has a chronic illness", body.GetProperty("content").GetString());
        Assert.True(body.GetProperty("user_verified").GetBoolean());
        Assert.False(body.GetProperty("pending_confirmation").GetBoolean());
    }

    [Fact]
    public async Task Delete_TombstonesAndMarksDeleted_AndNeverReappearsInList()
    {
        await using var fixture = await MemoryTestFixture.CreateAsync();
        var userId = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(userId);
        await fixture.Database.SeedProfileAsync(userId, "person@example.com");
        var memoryId = await fixture.Database.SeedMemoryAsync(userId, "has a big interview Friday at Acme");

        var client = fixture.CreateAuthorizedClient(userId);
        var response = await client.DeleteAsync($"/memory/{memoryId}");
        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);

        var status = await fixture.Database.FetchMemoryStatusAsync(memoryId);
        Assert.NotNull(status);
        Assert.Equal("deleted", status!.Value.Status);
        Assert.False(status.Value.HasEmbedding);
        Assert.Equal(1, await fixture.Database.CountTombstonesAsync(userId));

        var listResponse = await client.GetAsync("/memory");
        var body = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, body.GetArrayLength());
    }

    [Fact]
    public async Task CrossUser_CannotCorrectOrForgetAnotherUsersMemory()
    {
        await using var fixture = await MemoryTestFixture.CreateAsync();
        var owner = Guid.NewGuid();
        var attacker = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(owner);
        await fixture.Database.SeedProfileAsync(owner, "owner@example.com");
        await fixture.Database.SeedAuthUserAsync(attacker);
        await fixture.Database.SeedProfileAsync(attacker, "attacker@example.com");
        var memoryId = await fixture.Database.SeedMemoryAsync(owner, "has a big interview Friday at Acme");

        var attackerClient = fixture.CreateAuthorizedClient(attacker);

        var patchResponse = await attackerClient.PatchAsync(
            $"/memory/{memoryId}", JsonContent.Create(new { content = "tampered" }));
        Assert.Equal(HttpStatusCode.NotFound, patchResponse.StatusCode);

        var deleteResponse = await attackerClient.DeleteAsync($"/memory/{memoryId}");
        Assert.Equal(HttpStatusCode.NotFound, deleteResponse.StatusCode);

        var listResponse = await attackerClient.GetAsync("/memory");
        var body = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, body.GetArrayLength());

        // Control: the owner's own view and data are untouched by the attempts.
        var status = await fixture.Database.FetchMemoryStatusAsync(memoryId);
        Assert.Equal("active", status!.Value.Status);
    }

    private sealed class MemoryTestFixture : IAsyncDisposable
    {
        public TestDatabase Database { get; }
        private readonly TestJwt _jwt;
        private readonly MemoryApiFactory _factory;

        private MemoryTestFixture(TestDatabase database, TestJwt jwt, MemoryApiFactory factory)
        {
            Database = database;
            _jwt = jwt;
            _factory = factory;
        }

        public static async Task<MemoryTestFixture> CreateAsync()
        {
            var databaseName = $"kizuna_memory_test_{Guid.NewGuid():N}";
            var database = await TestDatabase.CreateAsync(databaseName);
            var jwt = new TestJwt();
            var factory = new MemoryApiFactory(database.ConnectionUrl, jwt.JwksJson);
            return new MemoryTestFixture(database, jwt, factory);
        }

        public HttpClient CreateAuthorizedClient(Guid userId)
        {
            var client = _factory.CreateClient();
            var token = _jwt.CreateToken(subject: userId.ToString());
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            return client;
        }

        public async ValueTask DisposeAsync()
        {
            _factory.Dispose();
            _jwt.Dispose();
            await Database.DisposeAsync();
        }
    }

    private sealed class MemoryApiFactory : WebApplicationFactory<Program>
    {
        private readonly string _jwksJson;

        public MemoryApiFactory(string databaseUrl, string jwksJson)
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
            Environment.SetEnvironmentVariable("FRONTEND_ORIGIN", "http://localhost:5173");
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
