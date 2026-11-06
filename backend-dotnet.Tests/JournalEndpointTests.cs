using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.Extensions.DependencyInjection;
using Npgsql;

namespace backend_dotnet.Tests;

// Phase 4 Step 4.2: .NET owns journal writes directly (frozen contract #1).
// No Python process is needed here -- POST's atomicity (entry + outbox row)
// is a single Postgres transaction entirely inside .NET; the worker that
// later claims the outbox row is Phase 3/4.2's own Python test suite's job
// to prove, not this file's.
public sealed class JournalEndpointTests
{
    [Fact]
    public async Task Post_CreatesEntryAndEnqueuesExtractionJob_Atomically()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("journal-post@example.com");
        var client = fixture.AuthorizedClient(userId);

        var response = await client.PostAsJsonAsync(
            "/journal", new { body = "Today was a good day.", mood = "good", idempotency_key = "key-1" });
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var created = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("Today was a good day.", created.GetProperty("body").GetString());
        Assert.Equal("good", created.GetProperty("mood").GetString());
        var entryId = Guid.Parse(created.GetProperty("id").GetString()!);

        var outboxRows = await fixture.FetchOutboxForSubjectAsync(entryId);
        Assert.Equal(1, outboxRows);
    }

    [Fact]
    public async Task Post_IsIdempotent_SameKeyReturnsExistingEntry_NoDuplicateOutboxRow()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("journal-idempotent@example.com");
        var client = fixture.AuthorizedClient(userId);

        var first = await client.PostAsJsonAsync(
            "/journal", new { body = "First version.", mood = (string?)null, idempotency_key = "same-key" });
        var firstBody = await first.Content.ReadFromJsonAsync<JsonElement>();
        var entryId = Guid.Parse(firstBody.GetProperty("id").GetString()!);

        var second = await client.PostAsJsonAsync(
            "/journal", new { body = "A different body entirely.", mood = "great", idempotency_key = "same-key" });
        var secondBody = await second.Content.ReadFromJsonAsync<JsonElement>();

        Assert.Equal(entryId.ToString(), secondBody.GetProperty("id").GetString());
        Assert.Equal("First version.", secondBody.GetProperty("body").GetString()); // the retry never overwrote it

        Assert.Equal(1, await fixture.FetchOutboxForSubjectAsync(entryId)); // never enqueued twice
    }

    // Step 4.5 live-verification finding: a freshly signed-up user (an
    // auth.users row with no profiles row yet, since the frontend never
    // calls /me) must succeed on the first write wherever it lands, not just
    // when it happens to hit /me first.
    [Fact]
    public async Task Post_Succeeds_ForFreshlySignedUpUser_WithNoPriorProfileRow()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = Guid.NewGuid();
        await fixture.Database.SeedAuthUserAsync(userId); // no SeedProfileAsync
        var client = fixture.AuthorizedClient(userId);

        var response = await client.PostAsJsonAsync(
            "/journal", new { body = "First entry ever.", mood = (string?)null, idempotency_key = "fresh-user-key" });
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        await using var connection = new NpgsqlConnection(fixture.Database.AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select count(*) from profiles where id = @id";
        command.Parameters.AddWithValue("id", userId);
        Assert.Equal(1L, await command.ExecuteScalarAsync());
    }

    [Fact]
    public async Task Get_ListsEntriesNewestFirst()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("journal-list@example.com");
        var client = fixture.AuthorizedClient(userId);

        await client.PostAsJsonAsync("/journal", new { body = "Entry one.", mood = (string?)null, idempotency_key = "k1" });
        await client.PostAsJsonAsync("/journal", new { body = "Entry two.", mood = (string?)null, idempotency_key = "k2" });

        var response = await client.GetAsync("/journal");
        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        var entries = body.EnumerateArray().ToList();
        Assert.Equal(2, entries.Count);
        Assert.Equal("Entry two.", entries[0].GetProperty("body").GetString());
        Assert.Equal("Entry one.", entries[1].GetProperty("body").GetString());
    }

    [Fact]
    public async Task Patch_UpdatesBodyAndMood_WithoutEnqueueingANewExtractionJob()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("journal-patch@example.com");
        var client = fixture.AuthorizedClient(userId);

        var created = await client.PostAsJsonAsync(
            "/journal", new { body = "Original text.", mood = "okay", idempotency_key = "k1" });
        var createdBody = await created.Content.ReadFromJsonAsync<JsonElement>();
        var entryId = Guid.Parse(createdBody.GetProperty("id").GetString()!);

        var patched = await client.PatchAsync(
            $"/journal/{entryId}", JsonContent.Create(new { body = "Edited text.", mood = "low" }));
        Assert.True(patched.IsSuccessStatusCode, await patched.Content.ReadAsStringAsync());
        var patchedBody = await patched.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("Edited text.", patchedBody.GetProperty("body").GetString());
        Assert.Equal("low", patchedBody.GetProperty("mood").GetString());

        // Editing does NOT re-run extraction (db/README.md's documented
        // limitation): still exactly the one job from creation.
        Assert.Equal(1, await fixture.FetchOutboxForSubjectAsync(entryId));
    }

    [Fact]
    public async Task Delete_RemovesEntry_SecondDeleteIs404()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var userId = await fixture.SeedUserAsync("journal-delete@example.com");
        var client = fixture.AuthorizedClient(userId);

        var created = await client.PostAsJsonAsync(
            "/journal", new { body = "Delete me.", mood = (string?)null, idempotency_key = "k1" });
        var createdBody = await created.Content.ReadFromJsonAsync<JsonElement>();
        var entryId = createdBody.GetProperty("id").GetString();

        var deleted = await client.DeleteAsync($"/journal/{entryId}");
        Assert.Equal(HttpStatusCode.NoContent, deleted.StatusCode);

        var secondDelete = await client.DeleteAsync($"/journal/{entryId}");
        Assert.Equal(HttpStatusCode.NotFound, secondDelete.StatusCode);

        var listResponse = await client.GetAsync("/journal");
        var listBody = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, listBody.GetArrayLength());
    }

    [Fact]
    public async Task CrossUser_CannotReadUpdateOrDeleteAnotherUsersEntry()
    {
        await using var fixture = await JournalTestFixture.CreateAsync();
        var owner = await fixture.SeedUserAsync("journal-owner@example.com");
        var attacker = await fixture.SeedUserAsync("journal-attacker@example.com");

        var ownerClient = fixture.AuthorizedClient(owner);
        var created = await ownerClient.PostAsJsonAsync(
            "/journal", new { body = "Private thoughts.", mood = (string?)null, idempotency_key = "k1" });
        var createdBody = await created.Content.ReadFromJsonAsync<JsonElement>();
        var entryId = createdBody.GetProperty("id").GetString();

        var attackerClient = fixture.AuthorizedClient(attacker);
        Assert.Equal(HttpStatusCode.NotFound, (await attackerClient.GetAsync($"/journal/{entryId}")).StatusCode);
        Assert.Equal(
            HttpStatusCode.NotFound,
            (await attackerClient.PatchAsync($"/journal/{entryId}", JsonContent.Create(new { body = "tampered", mood = (string?)null }))).StatusCode);
        Assert.Equal(HttpStatusCode.NotFound, (await attackerClient.DeleteAsync($"/journal/{entryId}")).StatusCode);

        var attackerList = await (await attackerClient.GetAsync("/journal")).Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, attackerList.GetArrayLength());
    }

    private sealed class JournalTestFixture : IAsyncDisposable
    {
        public TestDatabase Database { get; }
        private readonly TestJwt _jwt;
        private readonly JournalApiFactory _factory;

        private JournalTestFixture(TestDatabase database, TestJwt jwt, JournalApiFactory factory)
        {
            Database = database;
            _jwt = jwt;
            _factory = factory;
        }

        public static async Task<JournalTestFixture> CreateAsync()
        {
            var database = await TestDatabase.CreateAsync($"kizuna_journal_test_{Guid.NewGuid():N}");
            var jwt = new TestJwt();
            var factory = new JournalApiFactory(database.ConnectionUrl, jwt.JwksJson);
            return new JournalTestFixture(database, jwt, factory);
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

        // outbox.turn_id doubles as a generic "subject id" for this job type
        // (see JournalRepository.CreateAsync) -- it holds the journal
        // entry's own id here.
        public async Task<int> FetchOutboxForSubjectAsync(Guid subjectId)
        {
            await using var connection = new NpgsqlConnection(Database.AdoConnectionString);
            await connection.OpenAsync();
            await using var command = connection.CreateCommand();
            command.CommandText = "select count(*) from outbox where turn_id = @id and job_type = 'journal_memory_extraction'";
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

    private sealed class JournalApiFactory : Microsoft.AspNetCore.Mvc.Testing.WebApplicationFactory<Program>
    {
        private readonly string _jwksJson;

        public JournalApiFactory(string databaseUrl, string jwksJson)
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
