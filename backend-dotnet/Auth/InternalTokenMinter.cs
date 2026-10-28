using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text;
using Microsoft.IdentityModel.Tokens;

namespace backend_dotnet.Auth;

// Mints the short-lived internal service token Python verifies (PLAN.md
// section 17): HS256, claims {iss, aud, sub, scope, jti, iat, exp, turn_id}.
// Scoped to exactly one turn and one operation (chat:generate).
public sealed class InternalTokenMinter(InternalTokenOptions options)
{
    private static readonly TimeSpan Lifetime = TimeSpan.FromSeconds(60);

    public string MintChatGenerateToken(Guid userId, Guid turnId)
    {
        var key = new SymmetricSecurityKey(Encoding.UTF8.GetBytes(options.SigningKey));
        var credentials = new SigningCredentials(key, SecurityAlgorithms.HmacSha256);
        var now = DateTimeOffset.UtcNow;

        var claims = new[]
        {
            new Claim("sub", userId.ToString()),
            new Claim("scope", "chat:generate"),
            new Claim("jti", Guid.NewGuid().ToString()),
            new Claim(JwtRegisteredClaimNames.Iat, now.ToUnixTimeSeconds().ToString(), ClaimValueTypes.Integer64),
            new Claim("turn_id", turnId.ToString()),
        };

        var token = new JwtSecurityToken(
            issuer: options.Issuer,
            audience: options.Audience,
            claims: claims,
            notBefore: now.UtcDateTime,
            expires: now.Add(Lifetime).UtcDateTime,
            signingCredentials: credentials);

        return new JwtSecurityTokenHandler().WriteToken(token);
    }
}
