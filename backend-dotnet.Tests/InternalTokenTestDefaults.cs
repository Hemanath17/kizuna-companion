namespace backend_dotnet.Tests;

// Shared internal-token env values for tests that boot the real app but
// don't exercise /chat's call to Python (it still needs these to start).
public static class InternalTokenTestDefaults
{
    public const string SigningKey = "test-only-internal-signing-key-at-least-32-bytes-long";
    public const string Issuer = "kizuna-dotnet-gateway";
    public const string Audience = "kizuna-agent";
}
