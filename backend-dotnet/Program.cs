using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json.Serialization;
using backend_dotnet.Auth;
using backend_dotnet.Data;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.IdentityModel.Tokens;
using Npgsql;

LoadDotEnv(Path.Combine(Directory.GetCurrentDirectory(), ".env"));

var builder = WebApplication.CreateBuilder(args);

var supabaseOptions = new SupabaseOptions
{
    Url = RequireEnv("SUPABASE_URL"),
    JwksUrl = RequireEnv("SUPABASE_JWKS_URL"),
    Issuer = RequireEnv("SUPABASE_ISSUER"),
    Audience = RequireEnv("SUPABASE_AUDIENCE"),
};
builder.Services.AddSingleton(supabaseOptions);

builder.Services.AddSingleton<HttpMessageHandler>(_ => new HttpClientHandler());
builder.Services.AddSingleton<JwksKeyProvider>();

builder.Services.AddSingleton(NpgsqlDataSource.Create(PostgresConnectionString.FromUrl(RequireEnv("DATABASE_URL"))));
builder.Services.AddSingleton<IProfileRepository, ProfileRepository>();
builder.Services.AddSingleton<IChatRepository, ChatRepository>();

builder.Services.AddSingleton(new InternalTokenOptions
{
    SigningKey = RequireEnv("INTERNAL_SIGNING_KEY"),
    Issuer = RequireEnv("INTERNAL_TOKEN_ISSUER"),
    Audience = RequireEnv("INTERNAL_TOKEN_AUDIENCE"),
});
builder.Services.AddSingleton<InternalTokenMinter>();

builder.Services.AddHttpClient("agent-python", client =>
{
    client.BaseAddress = new Uri(RequireEnv("AGENT_PYTHON_URL"));
});

builder.Services
    .AddAuthentication(JwtBearerDefaults.AuthenticationScheme)
    .AddJwtBearer();

builder.Services.AddOptions<JwtBearerOptions>(JwtBearerDefaults.AuthenticationScheme)
    .Configure<JwksKeyProvider, SupabaseOptions>((options, jwks, supabase) =>
    {
        options.MapInboundClaims = false;
        options.TokenValidationParameters = new TokenValidationParameters
        {
            ValidateIssuer = true,
            ValidIssuer = supabase.Issuer,
            ValidateAudience = true,
            ValidAudience = supabase.Audience,
            ValidateLifetime = true,
            ValidateIssuerSigningKey = true,
            IssuerSigningKeyResolver = (_, _, _, _) => jwks.GetSigningKeys(),
        };
    });

builder.Services.AddAuthorization();

var app = builder.Build();

app.UseAuthentication();
app.UseAuthorization();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapGet("/me", async (ClaimsPrincipal user, IProfileRepository profiles, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    var email = user.FindFirst("email")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        return Results.Unauthorized();
    }

    await profiles.EnsureProfileAsync(userId, email, cancellationToken);
    return Results.Ok(new { user_id = sub, email });
}).RequireAuthorization();

app.MapPost("/chat", async (
    ChatRequest body,
    HttpContext context,
    ClaimsPrincipal user,
    IChatRepository chatRepository,
    InternalTokenMinter tokenMinter,
    IHttpClientFactory httpClientFactory) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        return;
    }

    Guid? requestedConversationId = null;
    if (!string.IsNullOrEmpty(body.ConversationId))
    {
        if (!Guid.TryParse(body.ConversationId, out var parsedConversationId))
        {
            context.Response.StatusCode = StatusCodes.Status400BadRequest;
            return;
        }
        requestedConversationId = parsedConversationId;
    }

    // Covers both "doesn't exist" and "belongs to another user" the same
    // way, so a cross-user probe can't distinguish the two.
    var conversationId = await chatRepository.EnsureConversationAsync(
        userId, requestedConversationId, context.RequestAborted);
    if (conversationId is null)
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    var accepted = await chatRepository.AcceptUserMessageAsync(
        userId, conversationId.Value, body.Text, body.IdempotencyKey, context.RequestAborted);

    if (!accepted.IsNew)
    {
        // PLAN.md section 16 item 3: reject-as-in-progress, no second turn,
        // no call to Python for a request that was already accepted once.
        context.Response.StatusCode = StatusCodes.Status409Conflict;
        await context.Response.WriteAsJsonAsync(new
        {
            turn_id = accepted.TurnId,
            status = accepted.Status,
            conversation_id = conversationId,
            sequence_number = accepted.SequenceNumber,
        }, context.RequestAborted);
        return;
    }

    var internalToken = tokenMinter.MintChatGenerateToken(userId, accepted.TurnId);

    using var pythonRequest = new HttpRequestMessage(HttpMethod.Post, "chat/stream")
    {
        Content = JsonContent.Create(new
        {
            conversation_id = conversationId,
            turn_id = accepted.TurnId,
            text = body.Text,
            sequence_number = accepted.SequenceNumber,
        }),
    };
    pythonRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", internalToken);

    context.Response.ContentType = "text/event-stream";
    context.Response.Headers.CacheControl = "no-cache";

    var httpClient = httpClientFactory.CreateClient("agent-python");

    HttpResponseMessage pythonResponse;
    try
    {
        pythonResponse = await httpClient.SendAsync(
            pythonRequest, HttpCompletionOption.ResponseHeadersRead, context.RequestAborted);
    }
    catch (OperationCanceledException)
    {
        // Browser disconnected before Python even answered; nothing to relay.
        // The user message is already durable (persisted at acceptance above).
        return;
    }

    using (pythonResponse)
    {
        if (!pythonResponse.IsSuccessStatusCode)
        {
            await WriteSseEventAsync(
                context.Response,
                "error",
                new { message = $"agent rejected the request ({(int)pythonResponse.StatusCode})" },
                context.RequestAborted);
            return;
        }

        try
        {
            var upstream = await pythonResponse.Content.ReadAsStreamAsync(context.RequestAborted);
            var buffer = new byte[4096];
            int bytesRead;
            while ((bytesRead = await upstream.ReadAsync(buffer, context.RequestAborted)) > 0)
            {
                await context.Response.Body.WriteAsync(buffer.AsMemory(0, bytesRead), context.RequestAborted);
                await context.Response.Body.FlushAsync(context.RequestAborted);
            }
        }
        catch (OperationCanceledException)
        {
            // Browser disconnected mid-stream. The same token cancelled the
            // downstream call to Python, so Python's own disconnect handling
            // marks the turn interrupted; the user message was already
            // durable before Python was ever called.
        }
    }
}).RequireAuthorization();

app.Run();

static async Task WriteSseEventAsync(HttpResponse response, string eventName, object data, CancellationToken cancellationToken)
{
    var json = System.Text.Json.JsonSerializer.Serialize(data);
    await response.WriteAsync($"event: {eventName}\ndata: {json}\n\n", cancellationToken);
    await response.Body.FlushAsync(cancellationToken);
}

static string RequireEnv(string key) =>
    Environment.GetEnvironmentVariable(key)
        ?? throw new InvalidOperationException($"Missing required environment variable: {key}");

// Dev convenience only: ASP.NET Core has no built-in .env loader. Real env
// vars (CI, hosting) always win over the file.
static void LoadDotEnv(string path)
{
    if (!File.Exists(path))
    {
        return;
    }

    foreach (var line in File.ReadAllLines(path))
    {
        var trimmed = line.Trim();
        if (trimmed.Length == 0 || trimmed.StartsWith('#'))
        {
            continue;
        }

        var separatorIndex = trimmed.IndexOf('=');
        if (separatorIndex < 0)
        {
            continue;
        }

        var key = trimmed[..separatorIndex].Trim();
        var value = trimmed[(separatorIndex + 1)..].Trim();
        if (Environment.GetEnvironmentVariable(key) is null)
        {
            Environment.SetEnvironmentVariable(key, value);
        }
    }
}

public partial class Program;

public sealed record ChatRequest(
    [property: JsonPropertyName("conversation_id")] string? ConversationId,
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("idempotency_key")] string IdempotencyKey);
