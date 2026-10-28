using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Microsoft.IdentityModel.Tokens;

namespace backend_dotnet.Tests;

// Stands in for Supabase: a real RSA key pair, a JWKS JSON blob for the
// matching public key (served to the app by StubHttpMessageHandler), and a
// token builder that signs with the private half.
public sealed class TestJwt : IDisposable
{
    public const string KeyId = "test-kid";
    public const string Issuer = "https://test.supabase.co/auth/v1";
    public const string Audience = "authenticated";

    private readonly RSA _rsa = RSA.Create(2048);

    public string JwksJson
    {
        get
        {
            var parameters = _rsa.ExportParameters(includePrivateParameters: false);
            var jwks = new
            {
                keys = new[]
                {
                    new
                    {
                        kty = "RSA",
                        kid = KeyId,
                        use = "sig",
                        alg = "RS256",
                        n = Base64UrlEncoder.Encode(parameters.Modulus),
                        e = Base64UrlEncoder.Encode(parameters.Exponent),
                    },
                },
            };
            return JsonSerializer.Serialize(jwks);
        }
    }

    public string CreateToken(
        string? subject = "11111111-1111-1111-1111-111111111111",
        string? email = "person@example.com",
        string issuer = Issuer,
        string audience = Audience,
        TimeSpan? expiresIn = null)
    {
        var signingKey = new RsaSecurityKey(_rsa) { KeyId = KeyId };
        var credentials = new SigningCredentials(signingKey, SecurityAlgorithms.RsaSha256);

        var claims = new List<Claim>();
        if (subject is not null)
        {
            claims.Add(new Claim("sub", subject));
        }
        if (email is not null)
        {
            claims.Add(new Claim("email", email));
        }

        var token = new JwtSecurityToken(
            issuer: issuer,
            audience: audience,
            claims: claims,
            expires: DateTime.UtcNow.Add(expiresIn ?? TimeSpan.FromHours(1)),
            signingCredentials: credentials);

        return new JwtSecurityTokenHandler().WriteToken(token);
    }

    // Flips a byte in the signature segment so the token fails verification
    // against the correct key, simulating a tampered/forged token.
    public static string Tamper(string token)
    {
        var parts = token.Split('.');
        var signatureBytes = Base64UrlEncoder.DecodeBytes(parts[2]);
        signatureBytes[0] ^= 0xFF;
        parts[2] = Base64UrlEncoder.Encode(signatureBytes);
        return string.Join('.', parts);
    }

    public void Dispose() => _rsa.Dispose();
}
