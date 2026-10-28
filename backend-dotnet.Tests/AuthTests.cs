using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;

namespace backend_dotnet.Tests;

public sealed class AuthTests : IClassFixture<AuthApiFactory>
{
    private readonly AuthApiFactory _factory;

    public AuthTests(AuthApiFactory factory) => _factory = factory;

    [Fact]
    public async Task Health_IsPublic()
    {
        var client = _factory.CreateClient();
        var response = await client.GetAsync("/health");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
    }

    [Fact]
    public async Task Me_WithoutToken_Returns401()
    {
        var client = _factory.CreateClient();
        var response = await client.GetAsync("/me");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Me_WithTamperedToken_Returns401()
    {
        var client = _factory.CreateClient();
        var token = TestJwt.Tamper(_factory.Jwt.CreateToken());
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.GetAsync("/me");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Me_WithExpiredToken_Returns401()
    {
        var client = _factory.CreateClient();
        var token = _factory.Jwt.CreateToken(expiresIn: TimeSpan.FromHours(-1));
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.GetAsync("/me");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Me_WithWrongAudience_Returns401()
    {
        var client = _factory.CreateClient();
        var token = _factory.Jwt.CreateToken(audience: "some-other-audience");
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.GetAsync("/me");

        Assert.Equal(HttpStatusCode.Unauthorized, response.StatusCode);
    }

    [Fact]
    public async Task Me_WithValidToken_ReturnsUserIdAndEmail()
    {
        var client = _factory.CreateClient();
        const string userId = "22222222-2222-2222-2222-222222222222";
        const string email = "valid@example.com";
        var token = _factory.Jwt.CreateToken(subject: userId, email: email);
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);

        var response = await client.GetAsync("/me");

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        var body = JsonSerializer.Deserialize<JsonElement>(await response.Content.ReadAsStringAsync());
        Assert.Equal(userId, body.GetProperty("user_id").GetString());
        Assert.Equal(email, body.GetProperty("email").GetString());

        Assert.Contains(_factory.Profiles.Calls, call => call.UserId == Guid.Parse(userId) && call.Email == email);
    }
}
