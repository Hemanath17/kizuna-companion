using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace backend_dotnet.Tests;

// Phase 4 Step 4.3: .NET owns reflection acceptance (window computation,
// idempotent insert, enqueuing 'reflection_generate'). No Python process is
// needed -- generation/validation is Python's own worker test suite's job.
public sealed class ReflectionEndpointTests
{
    [Fact]
    public async Task Post_ComputesWindow_InsertsPendingRow_AndEnqueuesOneJob()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-post@example.com");
        var client = fixture.AuthorizedClient(userId);

        var response = await client.PostAsJsonAsync("/reflections", new { period = "week" });
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("pending", body.GetProperty("status").GetString());
        var reflectionId = Guid.Parse(body.GetProperty("id").GetString()!);

        Assert.Equal(1, await fixture.FetchOutboxForSubjectAsync(reflectionId));
    }

    [Fact]
    public async Task Post_RepeatedForTheSameWindow_ReturnsExistingRow_NoDuplicateJob()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-idempotent@example.com");
        var client = fixture.AuthorizedClient(userId);

        var first = await client.PostAsJsonAsync("/reflections", new { period = "day" });
        var firstBody = await first.Content.ReadFromJsonAsync<JsonElement>();
        var reflectionId = firstBody.GetProperty("id").GetString();

        var second = await client.PostAsJsonAsync("/reflections", new { period = "day" });
        var secondBody = await second.Content.ReadFromJsonAsync<JsonElement>();

        Assert.Equal(reflectionId, secondBody.GetProperty("id").GetString());
        Assert.Equal(1, await fixture.FetchOutboxForSubjectAsync(Guid.Parse(reflectionId!)));
    }

    [Fact]
    public async Task Post_RejectsAnUnknownPeriod()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-badperiod@example.com");
        var client = fixture.AuthorizedClient(userId);

        var response = await client.PostAsJsonAsync("/reflections", new { period = "month" });
        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
    }

    [Fact]
    public async Task Get_List_And_GetById_NeverSurfaceARejectedReflection()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-hidden@example.com");
        var now = DateTimeOffset.UtcNow;
        var readyId = await fixture.Database.SeedReflectionAsync(userId, now.AddDays(-7), now, status: "ready", body: "A ready reflection.");
        var rejectedId = await fixture.Database.SeedReflectionAsync(userId, now.AddDays(-14), now.AddDays(-7), status: "rejected", rejectionReason: "dependency_language:x");

        var client = fixture.AuthorizedClient(userId);

        var listResponse = await client.GetAsync("/reflections");
        var list = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        var ids = list.EnumerateArray().Select(e => e.GetProperty("id").GetString()).ToHashSet();
        Assert.Contains(readyId.ToString(), ids);
        Assert.DoesNotContain(rejectedId.ToString(), ids);

        Assert.Equal(HttpStatusCode.OK, (await client.GetAsync($"/reflections/{readyId}")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync($"/reflections/{rejectedId}")).StatusCode);
    }

    [Fact]
    public async Task Delete_RemovesAReflection_EvenARejectedOne()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-delete@example.com");
        var now = DateTimeOffset.UtcNow;
        var rejectedId = await fixture.Database.SeedReflectionAsync(userId, now.AddDays(-7), now, status: "rejected", rejectionReason: "clinical_language:x");

        var client = fixture.AuthorizedClient(userId);
        var response = await client.DeleteAsync($"/reflections/{rejectedId}");
        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);

        var secondDelete = await client.DeleteAsync($"/reflections/{rejectedId}");
        Assert.Equal(HttpStatusCode.NotFound, secondDelete.StatusCode);
    }

    [Fact]
    public async Task Notes_CanBeAddedToAVisibleReflection_ButNotToARejectedOne()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("reflect-notes@example.com");
        var now = DateTimeOffset.UtcNow;
        var readyId = await fixture.Database.SeedReflectionAsync(userId, now.AddDays(-7), now, status: "ready", body: "A ready reflection.");
        var rejectedId = await fixture.Database.SeedReflectionAsync(userId, now.AddDays(-14), now.AddDays(-7), status: "rejected", rejectionReason: "x");

        var client = fixture.AuthorizedClient(userId);

        var okResponse = await client.PostAsJsonAsync($"/reflections/{readyId}/notes", new { body = "Thanks for noticing that." });
        Assert.Equal(HttpStatusCode.NoContent, okResponse.StatusCode);

        var hiddenResponse = await client.PostAsJsonAsync($"/reflections/{rejectedId}/notes", new { body = "anything" });
        Assert.Equal(HttpStatusCode.NotFound, hiddenResponse.StatusCode);

        var notesResponse = await client.GetAsync($"/reflections/{readyId}/notes");
        Assert.True(notesResponse.IsSuccessStatusCode, await notesResponse.Content.ReadAsStringAsync());
        var notes = await notesResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(1, notes.GetArrayLength());
        Assert.Equal("Thanks for noticing that.", notes.EnumerateArray().Single().GetProperty("body").GetString());

        Assert.Equal(HttpStatusCode.NotFound, (await client.GetAsync($"/reflections/{rejectedId}/notes")).StatusCode);
    }

    [Fact]
    public async Task CrossUser_CannotReadOrDeleteAnotherUsersReflection()
    {
        await using var fixture = await ReflectionTestFixture.CreateAsync();
        var owner = await fixture.SeedUserAsync("reflect-owner@example.com");
        var attacker = await fixture.SeedUserAsync("reflect-attacker@example.com");
        var now = DateTimeOffset.UtcNow;
        var reflectionId = await fixture.Database.SeedReflectionAsync(owner, now.AddDays(-7), now, status: "ready", body: "Private.");

        var attackerClient = fixture.AuthorizedClient(attacker);
        Assert.Equal(HttpStatusCode.NotFound, (await attackerClient.GetAsync($"/reflections/{reflectionId}")).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await attackerClient.DeleteAsync($"/reflections/{reflectionId}")).StatusCode);

        var attackerList = await (await attackerClient.GetAsync("/reflections")).Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, attackerList.GetArrayLength());
    }

    private sealed class ReflectionTestFixture : IAsyncDisposable
    {
        public TestDatabase Database { get; }
        private readonly TestJwt _jwt;
        private readonly ReflectionApiFactory _factory;

        private ReflectionTestFixture(TestDatabase database, TestJwt jwt, ReflectionApiFactory factory)
        {
            Database = database;
            _jwt = jwt;
            _factory = factory;
        }

        public static async Task<ReflectionTestFixture> CreateAsync()
        {
            var database = await TestDatabase.CreateAsync($"kizuna_reflection_test_{Guid.NewGuid():N}");
            var jwt = new TestJwt();
            var factory = new ReflectionApiFactory(database.ConnectionUrl, jwt.JwksJson);
            return new ReflectionTestFixture(database, jwt, factory);
        }

        public async Task<Guid> SeedUserAsync(string email)
        {
            var userId = Guid.NewGuid();
            await Database.SeedAuthUserAsync(userId);
            await Database.SeedProfileAsync(userId, email);
            return userId;
        }

        public HttpClient AuthorizedClient(Guid userId)
        {
            var client = _factory.CreateClient();
            var token = _jwt.CreateToken(subject: userId.ToString());
            client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
            return client;
        }

        public async Task<int> FetchOutboxForSubjectAsync(Guid subjectId)
        {
            await using var connection = new NpgsqlConnection(Database.AdoConnectionString);
            await connection.OpenAsync();
            await using var command = connection.CreateCommand();
            command.CommandText = "select count(*) from outbox where turn_id = @id and job_type = 'reflection_generate'";
            command.Parameters.AddWithValue("id", subjectId);
            return (int)(long)(await command.ExecuteScalarAsync())!;
        }

        public async ValueTask DisposeAsync()
        {
            _factory.Dispose();
            _jwt.Dispose();
            await Database.DisposeAsync();
        }
    }

    private sealed class ReflectionApiFactory : Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactory<Program>
    {
        private readonly string _jwksJson;

        public ReflectionApiFactory(string databaseUrl, string jwksJson)
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
