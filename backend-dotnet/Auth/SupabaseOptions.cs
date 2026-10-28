namespace backend_dotnet.Auth;

public sealed class SupabaseOptions
{
    public required string Url { get; init; }
    public required string JwksUrl { get; init; }
    public required string Issuer { get; init; }
    public required string Audience { get; init; }
}
