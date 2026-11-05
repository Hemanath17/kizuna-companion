using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using Npgsql;

namespace backend_dotnet.Tests;

// Phase 4 Step 4.1 Part A: /memory is now a thin .NET proxy to Python,
// which owns the full memory lifecycle. These are real end-to-end tests --
// a real .NET app, a real spawned Python agent process, and a real local
// Postgres database shared by both (ChatE2EFixture) -- because a proxy is
// only proven correct by exercising the actual HTTP hop it performs.
public sealed class MemoryEndpointTests : IClassFixture<ChatE2EFixture>
{
    private readonly ChatE2EFixture _fixture;

    public MemoryEndpointTests(ChatE2EFixture fixture) => _fixture = fixture;

    private HttpClient AuthorizedClient(Guid userId)
    {
        var client = _fixture.CreateClient();
        var token = _fixture.Jwt.CreateToken(subject: userId.ToString());
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
        return client;
    }

    private async Task<Guid> SeedUserAsync(string email)
    {
        var userId = Guid.NewGuid();
        await _fixture.Database.SeedAuthUserAsync(userId);
        await _fixture.Database.SeedProfileAsync(userId, email);
        return userId;
    }

    private async Task<string?> FetchEmbeddingTextAsync(Guid memoryId)
    {
        await using var connection = new NpgsqlConnection(_fixture.Database.AdoConnectionString);
        await connection.OpenAsync();
        await using var command = connection.CreateCommand();
        command.CommandText = "select embedding::text from memory where id = @id";
        command.Parameters.AddWithValue("id", memoryId);
        var result = await command.ExecuteScalarAsync();
        return result as string;
    }

    [Fact]
    public async Task Get_ListsMemoriesWithProvenanceAndLabels_IncludingPending()
    {
        var userId = await SeedUserAsync("person-list@example.com");
        await _fixture.Database.SeedMemoryAsync(userId, "works as a data engineer");
        await _fixture.Database.SeedMemoryAsync(userId, "has a chronic illness", isSensitive: true, pendingConfirmation: true);

        var client = AuthorizedClient(userId);
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
    public async Task Patch_ThroughProxy_SupersedesWithFreshDistinctEmbedding_AndOldNeverReappearsInList()
    {
        var userId = await SeedUserAsync("person-patch@example.com");
        var memoryId = await _fixture.Database.SeedMemoryAsync(userId, "has a big interview Friday at Acme", memoryType: "episodic");
        var originalEmbedding = await FetchEmbeddingTextAsync(memoryId);
        Assert.Null(originalEmbedding); // seeded with no embedding, same as a freshly-extracted fact before this fix existed

        var client = AuthorizedClient(userId);
        var response = await client.PatchAsync(
            $"/memory/{memoryId}", JsonContent.Create(new { content = "has a big interview Thursday at Acme" }));
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("has a big interview Thursday at Acme", body.GetProperty("content").GetString());
        Assert.True(body.GetProperty("user_verified").GetBoolean());
        var newId = Guid.Parse(body.GetProperty("id").GetString()!);

        // The bug this fixes: the old in-place .NET update never recomputed
        // the embedding, so it kept pointing at the ORIGINAL wording. Here
        // the correction is a genuinely new row with its own, non-null,
        // freshly computed embedding -- which is what makes retrieval for
        // the corrected wording find it (proven at the embedding/retrieval
        // level in agent-python/tests/test_memory_lifecycle.py, since only
        // Python has retrieval machinery to exercise directly).
        var newEmbedding = await FetchEmbeddingTextAsync(newId);
        Assert.NotNull(newEmbedding);

        var listResponse = await client.GetAsync("/memory");
        var listed = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        var ids = listed.EnumerateArray().Select(e => e.GetProperty("id").GetString()).ToHashSet();
        Assert.Contains(newId.ToString(), ids);
        Assert.DoesNotContain(memoryId.ToString(), ids);

        var historyResponse = await client.GetAsync($"/memory/{newId}/history");
        var history = await historyResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(1, history.GetArrayLength());
        Assert.Equal(memoryId.ToString(), history.EnumerateArray().Single().GetProperty("id").GetString());
    }

    [Fact]
    public async Task Patch_ApprovesPendingSensitiveMemory_WithoutChangingContent()
    {
        var userId = await SeedUserAsync("person-approve@example.com");
        var memoryId = await _fixture.Database.SeedMemoryAsync(
            userId, "has a chronic illness", isSensitive: true, pendingConfirmation: true);

        var client = AuthorizedClient(userId);
        var response = await client.PatchAsync($"/memory/{memoryId}", JsonContent.Create(new { }));
        Assert.True(response.IsSuccessStatusCode, await response.Content.ReadAsStringAsync());

        var body = await response.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal("has a chronic illness", body.GetProperty("content").GetString());
        Assert.True(body.GetProperty("user_verified").GetBoolean());
        Assert.False(body.GetProperty("pending_confirmation").GetBoolean());
        Assert.Equal(memoryId.ToString(), body.GetProperty("id").GetString()); // same row -- no content change, no new row
    }

    [Fact]
    public async Task Patch_NonexistentMemory_PropagatesPythons404_NotMaskedAsA500()
    {
        var userId = await SeedUserAsync("person-404@example.com");
        var client = AuthorizedClient(userId);

        var response = await client.PatchAsync(
            $"/memory/{Guid.NewGuid()}", JsonContent.Create(new { content = "doesn't matter" }));
        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }

    [Fact]
    public async Task Delete_ThroughProxy_TombstonesAndMarksDeleted_AndNeverReappearsInList()
    {
        var userId = await SeedUserAsync("person-delete@example.com");
        var memoryId = await _fixture.Database.SeedMemoryAsync(userId, "has a big interview Friday at Acme");

        var client = AuthorizedClient(userId);
        var response = await client.DeleteAsync($"/memory/{memoryId}");
        Assert.Equal(HttpStatusCode.NoContent, response.StatusCode);

        var status = await _fixture.Database.FetchMemoryStatusAsync(memoryId);
        Assert.NotNull(status);
        Assert.Equal("deleted", status!.Value.Status);
        Assert.False(status.Value.HasEmbedding);
        Assert.Equal(1, await _fixture.Database.CountTombstonesAsync(userId));

        var listResponse = await client.GetAsync("/memory");
        var body = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, body.GetArrayLength());
    }

    [Fact]
    public async Task CrossUser_CannotCorrectOrForgetAnotherUsersMemory()
    {
        var owner = await SeedUserAsync("owner-memory@example.com");
        var attacker = await SeedUserAsync("attacker-memory@example.com");
        var memoryId = await _fixture.Database.SeedMemoryAsync(owner, "has a big interview Friday at Acme");

        var attackerClient = AuthorizedClient(attacker);

        var patchResponse = await attackerClient.PatchAsync(
            $"/memory/{memoryId}", JsonContent.Create(new { content = "tampered" }));
        Assert.Equal(HttpStatusCode.NotFound, patchResponse.StatusCode);

        var deleteResponse = await attackerClient.DeleteAsync($"/memory/{memoryId}");
        Assert.Equal(HttpStatusCode.NotFound, deleteResponse.StatusCode);

        var listResponse = await attackerClient.GetAsync("/memory");
        var body = await listResponse.Content.ReadFromJsonAsync<JsonElement>();
        Assert.Equal(0, body.GetArrayLength());

        // Control: the owner's own view and data are untouched by the attempts.
        var status = await _fixture.Database.FetchMemoryStatusAsync(memoryId);
        Assert.Equal("active", status!.Value.Status);
    }
}
