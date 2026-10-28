namespace backend_dotnet.Auth;

// Shared with agent-python's INTERNAL_SIGNING_KEY / INTERNAL_TOKEN_ISSUER /
// INTERNAL_TOKEN_AUDIENCE -- values must match exactly on both sides.
public sealed class InternalTokenOptions
{
    public required string SigningKey { get; init; }
    public required string Issuer { get; init; }
    public required string Audience { get; init; }
}
