using Microsoft.IdentityModel.Tokens;

namespace backend_dotnet.Auth;

// Supabase's JWKS endpoint has no OIDC discovery document to let JwtBearer
// auto-fetch it via Authority, so we fetch and cache it directly.
public sealed class JwksKeyProvider
{
    // Supabase's own edge cache in front of the JWKS endpoint is 10 minutes;
    // matching that avoids us being staler than the source we're caching.
    private static readonly TimeSpan CacheDuration = TimeSpan.FromMinutes(10);

    private readonly HttpClient _httpClient;
    private readonly string _jwksUrl;
    private readonly object _lock = new();
    private IReadOnlyList<SecurityKey> _cachedKeys = [];
    private DateTimeOffset _cachedAt = DateTimeOffset.MinValue;

    public JwksKeyProvider(HttpMessageHandler handler, SupabaseOptions options)
    {
        _httpClient = new HttpClient(handler, disposeHandler: false);
        _jwksUrl = options.JwksUrl;
    }

    // shortcut: blocking call on cache miss — IssuerSigningKeyResolver has no
    // async overload. Acceptable since JWKS rotates rarely and Supabase
    // already caches it for 10 min; move to IConfigurationManager<JsonWebKeySet>
    // if this ever shows up as a latency problem.
    public IEnumerable<SecurityKey> GetSigningKeys()
    {
        lock (_lock)
        {
            if (_cachedKeys.Count > 0 && DateTimeOffset.UtcNow - _cachedAt < CacheDuration)
            {
                return _cachedKeys;
            }

            var json = _httpClient.GetStringAsync(_jwksUrl).GetAwaiter().GetResult();
            var jwks = new JsonWebKeySet(json);
            _cachedKeys = jwks.GetSigningKeys().ToList();
            _cachedAt = DateTimeOffset.UtcNow;
            return _cachedKeys;
        }
    }
}
