using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

namespace backend_dotnet.Tests;

// Boots the real .NET app AND a real spawned Python agent process against
// one shared, real local Postgres database -- "real local Postgres + both
// services", as asked, not stubs standing in for either side.
public sealed class ChatE2EFixture : WebApplicationFactory<Program>, IAsyncLifetime
{
    public TestJwt Jwt { get; } = new();
    public TestDatabase Database { get; private set; } = null!;
    public PythonAgentProcess Agent { get; private set; } = null!;

    public async Task InitializeAsync()
    {
        Database = await TestDatabase.CreateAsync($"kizuna_chat_e2e_{Guid.NewGuid():N}");
        Agent = await PythonAgentProcess.StartAsync(
            Database.AgentDatabaseUrl,
            InternalTokenTestDefaults.SigningKey,
            InternalTokenTestDefaults.Issuer,
            InternalTokenTestDefaults.Audience);

        Environment.SetEnvironmentVariable("SUPABASE_URL", "https://test.supabase.co");
        Environment.SetEnvironmentVariable("SUPABASE_JWKS_URL", "https://test.supabase.co/auth/v1/.well-known/jwks.json");
        Environment.SetEnvironmentVariable("SUPABASE_ISSUER", TestJwt.Issuer);
        Environment.SetEnvironmentVariable("SUPABASE_AUDIENCE", TestJwt.Audience);
        Environment.SetEnvironmentVariable("DATABASE_URL", Database.ConnectionUrl);
        Environment.SetEnvironmentVariable("INTERNAL_SIGNING_KEY", InternalTokenTestDefaults.SigningKey);
        Environment.SetEnvironmentVariable("INTERNAL_TOKEN_ISSUER", InternalTokenTestDefaults.Issuer);
        Environment.SetEnvironmentVariable("INTERNAL_TOKEN_AUDIENCE", InternalTokenTestDefaults.Audience);
        Environment.SetEnvironmentVariable("AGENT_PYTHON_URL", Agent.BaseUrl);

        // Forces the host to build now, while the env vars above are current
        // (CollectionBehavior.DisableTestParallelization keeps them that way).
        _ = Server;
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            services.AddSingleton<HttpMessageHandler>(new StubHttpMessageHandler(Jwt.JwksJson));
        });
    }

    // Hides (not overrides) WebApplicationFactory's own IAsyncDisposable
    // member -- this is specifically the IAsyncLifetime method xunit calls
    // for fixture teardown, separate from that disposal path.
    public new async Task DisposeAsync()
    {
        await Agent.DisposeAsync();
        await Database.DisposeAsync();
        await base.DisposeAsync();
    }
}
