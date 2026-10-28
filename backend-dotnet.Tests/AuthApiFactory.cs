using backend_dotnet.Data;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;

namespace backend_dotnet.Tests;

// Boots the real app with a fake JWKS endpoint (StubHttpMessageHandler) and
// a fake profile repository, so auth behavior is tested without any network
// or database dependency.
public sealed class AuthApiFactory : WebApplicationFactory<Program>
{
    public TestJwt Jwt { get; } = new();
    public RecordingProfileRepository Profiles { get; } = new();

    public AuthApiFactory()
    {
        Environment.SetEnvironmentVariable("SUPABASE_URL", "https://test.supabase.co");
        Environment.SetEnvironmentVariable("SUPABASE_JWKS_URL", "https://test.supabase.co/auth/v1/.well-known/jwks.json");
        Environment.SetEnvironmentVariable("SUPABASE_ISSUER", TestJwt.Issuer);
        Environment.SetEnvironmentVariable("SUPABASE_AUDIENCE", TestJwt.Audience);
        Environment.SetEnvironmentVariable("DATABASE_URL", "postgres://user:pass@localhost:1/db");
        Environment.SetEnvironmentVariable("INTERNAL_SIGNING_KEY", InternalTokenTestDefaults.SigningKey);
        Environment.SetEnvironmentVariable("INTERNAL_TOKEN_ISSUER", InternalTokenTestDefaults.Issuer);
        Environment.SetEnvironmentVariable("INTERNAL_TOKEN_AUDIENCE", InternalTokenTestDefaults.Audience);
        Environment.SetEnvironmentVariable("AGENT_PYTHON_URL", "http://127.0.0.1:1");
    }

    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            services.AddSingleton<HttpMessageHandler>(new StubHttpMessageHandler(Jwt.JwksJson));
            services.AddSingleton<IProfileRepository>(Profiles);
        });
    }
}
