using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json.Serialization;
using backend_dotnet.Auth;
using backend_dotnet.Data;
using Microsoft.AspNetCore.Authentication.JwtBearer;
using Microsoft.Extensions.DependencyInjection;
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
builder.Services.AddSingleton<IJournalRepository, JournalRepository>();
builder.Services.AddSingleton<IReflectionRepository, ReflectionRepository>();

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

var frontendOrigin = RequireEnv("FRONTEND_ORIGIN");
builder.Services.AddCors(options =>
{
    options.AddDefaultPolicy(policy => policy
        .WithOrigins(frontendOrigin)
        .AllowAnyHeader()
        .AllowAnyMethod()
        .WithExposedHeaders("X-Conversation-Id", "X-Turn-Id"));
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

app.UseCors();
app.UseAuthentication();
app.UseAuthorization();

// Step 4.5 live-verification finding: EnsureProfileAsync used to run only
// inside the /me handler, which the frontend never calls -- a freshly signed
// up user's first write anywhere else (e.g. /chat, /journal, /reflections)
// hit a profiles FK violation and 500'd. Running it here, after
// authentication/authorization and before any endpoint handler, guarantees
// the row exists on the FIRST authenticated request no matter which endpoint
// it hits, so no future endpoint can reintroduce this bug by forgetting to
// call /me first. EnsureProfileAsync's INSERT ... ON CONFLICT DO NOTHING is
// cheap and idempotent, so paying for it on every request is the right trade
// for "no endpoint can forget this" over micro-optimizing the common case.
app.Use(async (context, next) =>
{
    if (context.User.Identity?.IsAuthenticated == true)
    {
        var sub = context.User.FindFirst("sub")?.Value;
        if (sub is not null && Guid.TryParse(sub, out var userId))
        {
            var email = context.User.FindFirst("email")?.Value;
            var profiles = context.RequestServices.GetRequiredService<IProfileRepository>();
            await profiles.EnsureProfileAsync(userId, email, context.RequestAborted);
        }
    }
    await next(context);
});

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));

app.MapGet("/me", (ClaimsPrincipal user) =>
{
    var sub = user.FindFirst("sub")?.Value;
    var email = user.FindFirst("email")?.Value;
    if (sub is null || !Guid.TryParse(sub, out _))
    {
        return Results.Unauthorized();
    }

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

    // Checks ownership of requestedConversationId (covering "doesn't exist"
    // and "belongs to another user" the same way, so a cross-user probe
    // can't distinguish the two), checks for a duplicate (user_id,
    // idempotency_key) BEFORE creating anything, and only creates a
    // conversation when accepting a genuinely new turn -- a blind retry with
    // no conversation_id never leaks an orphaned conversation row.
    var accepted = await chatRepository.AcceptUserMessageAsync(
        userId, requestedConversationId, body.Text, body.IdempotencyKey, context.RequestAborted);
    if (accepted is null)
    {
        context.Response.StatusCode = StatusCodes.Status404NotFound;
        return;
    }

    // Set before any body write so both the success stream and the 409 below
    // carry it; the only way the frontend learns the server-assigned
    // conversation_id/turn_id on a freshly-created conversation (PLAN.md's
    // SSE event contract doesn't carry them -- see Step 5 notes).
    context.Response.Headers["X-Conversation-Id"] = accepted.ConversationId.ToString();
    context.Response.Headers["X-Turn-Id"] = accepted.TurnId.ToString();

    if (!accepted.IsNew)
    {
        // PLAN.md section 16 item 3: reject-as-in-progress, no second turn,
        // no call to Python for a request that was already accepted once.
        context.Response.StatusCode = StatusCodes.Status409Conflict;
        await context.Response.WriteAsJsonAsync(new
        {
            turn_id = accepted.TurnId,
            status = accepted.Status,
            conversation_id = accepted.ConversationId,
            sequence_number = accepted.SequenceNumber,
        }, context.RequestAborted);
        return;
    }

    var internalToken = tokenMinter.MintChatGenerateToken(userId, accepted.TurnId);

    using var pythonRequest = new HttpRequestMessage(HttpMethod.Post, "chat/stream")
    {
        Content = JsonContent.Create(new
        {
            conversation_id = accepted.ConversationId,
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

// Phase 4 Step 4.1 Part A: Python owns the full memory lifecycle now
// (PLAN.md Phase 4 decision 1). These are thin authenticated proxies --
// verify the Supabase JWT, mint a scoped internal token, forward the
// request body/query untouched, and propagate Python's status code and
// body back verbatim (never masked as a generic 500).

app.MapGet("/memory", async (
    HttpContext context, ClaimsPrincipal user, InternalTokenMinter tokenMinter, IHttpClientFactory httpClientFactory) =>
{
    if (!TryGetUserId(user, context, out var userId))
    {
        return;
    }
    var token = tokenMinter.MintMemoryManageToken(userId);
    await ProxyToPythonAsync(context, httpClientFactory, HttpMethod.Get, $"memory{context.Request.QueryString}", token, null);
}).RequireAuthorization();

app.MapGet("/memory/{id}/history", async (
    string id, HttpContext context, ClaimsPrincipal user, InternalTokenMinter tokenMinter, IHttpClientFactory httpClientFactory) =>
{
    if (!TryGetUserId(user, context, out var userId))
    {
        return;
    }
    var token = tokenMinter.MintMemoryManageToken(userId);
    await ProxyToPythonAsync(context, httpClientFactory, HttpMethod.Get, $"memory/{id}/history", token, null);
}).RequireAuthorization();

app.MapPatch("/memory/{id}", async (
    string id,
    MemoryPatchRequest body,
    HttpContext context,
    ClaimsPrincipal user,
    InternalTokenMinter tokenMinter,
    IHttpClientFactory httpClientFactory) =>
{
    if (!TryGetUserId(user, context, out var userId))
    {
        return;
    }
    var token = tokenMinter.MintMemoryManageToken(userId);
    var content = JsonContent.Create(new { content = body.Content });
    await ProxyToPythonAsync(context, httpClientFactory, HttpMethod.Patch, $"memory/{id}", token, content);
}).RequireAuthorization();

app.MapDelete("/memory/{id}", async (
    string id, HttpContext context, ClaimsPrincipal user, InternalTokenMinter tokenMinter, IHttpClientFactory httpClientFactory) =>
{
    if (!TryGetUserId(user, context, out var userId))
    {
        return;
    }
    var token = tokenMinter.MintMemoryManageToken(userId);
    await ProxyToPythonAsync(context, httpClientFactory, HttpMethod.Delete, $"memory/{id}", token, null);
}).RequireAuthorization();

// Phase 4 Step 4.2: .NET owns journal writes directly (frozen contract #1 --
// this is raw user-authored content, not derived memory state).

app.MapPost("/journal", async (
    JournalCreateRequest body, ClaimsPrincipal user, IJournalRepository journal, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        return Results.Unauthorized();
    }
    var created = await journal.CreateAsync(userId, body.Body, body.Mood, body.IdempotencyKey, cancellationToken);
    return Results.Ok(JournalResponse(created));
}).RequireAuthorization();

app.MapGet("/journal", async (
    ClaimsPrincipal user, IJournalRepository journal, CancellationToken cancellationToken, int limit = 50, int offset = 0) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        return Results.Unauthorized();
    }
    var entries = await journal.ListAsync(userId, limit, offset, cancellationToken);
    return Results.Ok(entries.Select(JournalResponse));
}).RequireAuthorization();

app.MapGet("/journal/{id}", async (
    string id, ClaimsPrincipal user, IJournalRepository journal, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var entryId))
    {
        return Results.Unauthorized();
    }
    var entry = await journal.GetAsync(userId, entryId, cancellationToken);
    return entry is null ? Results.NotFound() : Results.Ok(JournalResponse(entry));
}).RequireAuthorization();

app.MapPatch("/journal/{id}", async (
    string id, JournalPatchRequest body, ClaimsPrincipal user, IJournalRepository journal, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var entryId))
    {
        return Results.Unauthorized();
    }
    var updated = await journal.UpdateAsync(userId, entryId, body.Body, body.Mood, cancellationToken);
    return updated is null ? Results.NotFound() : Results.Ok(JournalResponse(updated));
}).RequireAuthorization();

app.MapDelete("/journal/{id}", async (
    string id, ClaimsPrincipal user, IJournalRepository journal, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var entryId))
    {
        return Results.Unauthorized();
    }
    var deleted = await journal.DeleteAsync(userId, entryId, cancellationToken);
    return deleted ? Results.NoContent() : Results.NotFound();
}).RequireAuthorization();

// Phase 4 Step 4.3: on-demand only -- no scheduler, no cron. .NET computes
// the window server-side and enqueues 'reflection_generate'; the existing
// Phase 3 worker + reflection_agent/reflection_validator do the rest.

app.MapPost("/reflections", async (
    ReflectionCreateRequest body, ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        return Results.Unauthorized();
    }
    DateTimeOffset periodStart, periodEnd;
    try
    {
        (periodStart, periodEnd) = ComputeReflectionWindow(body.Period);
    }
    catch (ArgumentException)
    {
        return Results.BadRequest(new { message = "period must be 'day' or 'week'" });
    }
    var created = await reflections.CreateOrGetAsync(userId, periodStart, periodEnd, cancellationToken);
    return Results.Ok(ReflectionResponse(created));
}).RequireAuthorization();

app.MapGet("/reflections", async (
    ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId))
    {
        return Results.Unauthorized();
    }
    var list = await reflections.ListAsync(userId, cancellationToken);
    return Results.Ok(list.Select(ReflectionResponse));
}).RequireAuthorization();

app.MapGet("/reflections/{id}", async (
    string id, ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var reflectionId))
    {
        return Results.Unauthorized();
    }
    var reflection = await reflections.GetAsync(userId, reflectionId, cancellationToken);
    return reflection is null ? Results.NotFound() : Results.Ok(ReflectionResponse(reflection));
}).RequireAuthorization();

app.MapDelete("/reflections/{id}", async (
    string id, ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var reflectionId))
    {
        return Results.Unauthorized();
    }
    var deleted = await reflections.DeleteAsync(userId, reflectionId, cancellationToken);
    return deleted ? Results.NoContent() : Results.NotFound();
}).RequireAuthorization();

app.MapGet("/reflections/{id}/notes", async (
    string id, ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var reflectionId))
    {
        return Results.Unauthorized();
    }
    var notes = await reflections.ListNotesAsync(userId, reflectionId, cancellationToken);
    return notes is null
        ? Results.NotFound()
        : Results.Ok(notes.Select(n => new { id = n.Id, body = n.Body, created_at = n.CreatedAt }));
}).RequireAuthorization();

app.MapPost("/reflections/{id}/notes", async (
    string id, ReflectionNoteRequest body, ClaimsPrincipal user, IReflectionRepository reflections, CancellationToken cancellationToken) =>
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out var userId) || !Guid.TryParse(id, out var reflectionId))
    {
        return Results.Unauthorized();
    }
    var added = await reflections.AddNoteAsync(userId, reflectionId, body.Body, cancellationToken);
    return added ? Results.NoContent() : Results.NotFound();
}).RequireAuthorization();

app.Run();

static (DateTimeOffset Start, DateTimeOffset End) ComputeReflectionWindow(string period)
{
    var now = DateTimeOffset.UtcNow;
    var today = new DateTimeOffset(now.Year, now.Month, now.Day, 0, 0, 0, TimeSpan.Zero);
    if (period == "day")
    {
        return (today, today.AddDays(1));
    }
    if (period == "week")
    {
        // Monday-start week, computed from UTC midnight so repeat requests
        // within the same calendar day/week land on the identical window
        // and hit the (user_id, period_start, period_end) idempotency key.
        var daysSinceMonday = ((int)today.DayOfWeek + 6) % 7;
        var weekStart = today.AddDays(-daysSinceMonday);
        return (weekStart, weekStart.AddDays(7));
    }
    throw new ArgumentException($"unknown period: {period}");
}

static object ReflectionResponse(ReflectionRecord reflection) => new
{
    id = reflection.Id,
    period_start = reflection.PeriodStart,
    period_end = reflection.PeriodEnd,
    body = reflection.Body,
    status = reflection.Status,
    rejection_reason = reflection.RejectionReason,
    created_at = reflection.CreatedAt,
    generated_at = reflection.GeneratedAt,
};

static object JournalResponse(JournalEntryRecord entry) => new
{
    id = entry.Id,
    body = entry.Body,
    mood = entry.Mood,
    created_at = entry.CreatedAt,
    updated_at = entry.UpdatedAt,
};

static bool TryGetUserId(ClaimsPrincipal user, HttpContext context, out Guid userId)
{
    var sub = user.FindFirst("sub")?.Value;
    if (sub is null || !Guid.TryParse(sub, out userId))
    {
        context.Response.StatusCode = StatusCodes.Status401Unauthorized;
        userId = default;
        return false;
    }
    return true;
}

static async Task ProxyToPythonAsync(
    HttpContext context,
    IHttpClientFactory httpClientFactory,
    HttpMethod method,
    string pathAndQuery,
    string internalToken,
    HttpContent? content)
{
    using var pythonRequest = new HttpRequestMessage(method, pathAndQuery) { Content = content };
    pythonRequest.Headers.Authorization = new AuthenticationHeaderValue("Bearer", internalToken);

    var httpClient = httpClientFactory.CreateClient("agent-python");

    HttpResponseMessage pythonResponse;
    try
    {
        pythonResponse = await httpClient.SendAsync(pythonRequest, context.RequestAborted);
    }
    catch (OperationCanceledException)
    {
        return;
    }

    using (pythonResponse)
    {
        context.Response.StatusCode = (int)pythonResponse.StatusCode;
        var body = await pythonResponse.Content.ReadAsByteArrayAsync(context.RequestAborted);
        if (body.Length > 0)
        {
            context.Response.ContentType = pythonResponse.Content.Headers.ContentType?.ToString() ?? "application/json";
            await context.Response.Body.WriteAsync(body, context.RequestAborted);
        }
    }
}

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

public sealed record MemoryPatchRequest([property: JsonPropertyName("content")] string? Content);

public sealed record JournalCreateRequest(
    [property: JsonPropertyName("body")] string Body,
    [property: JsonPropertyName("mood")] string? Mood,
    [property: JsonPropertyName("idempotency_key")] string IdempotencyKey);

public sealed record JournalPatchRequest(
    [property: JsonPropertyName("body")] string Body,
    [property: JsonPropertyName("mood")] string? Mood);

public sealed record ReflectionCreateRequest([property: JsonPropertyName("period")] string Period);

public sealed record ReflectionNoteRequest([property: JsonPropertyName("body")] string Body);
