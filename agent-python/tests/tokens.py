import time
import uuid

import jwt

ISSUER = "kizuna-dotnet-gateway"
AUDIENCE = "kizuna-agent"
SIGNING_KEY = "test-only-internal-signing-key-at-least-32-bytes-long"


def mint_token(
    *,
    user_id: str,
    turn_id: str,
    scope: str = "chat:generate",
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expires_in: int = 60,
    signing_key: str = SIGNING_KEY,
    overrides: dict | None = None,
) -> str:
    now = int(time.time())
    claims = {
        "iss": issuer,
        "aud": audience,
        "sub": user_id,
        "scope": scope,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + expires_in,
        "turn_id": turn_id,
    }
    if overrides:
        claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="HS256")


def tamper(token: str) -> str:
    header, payload, signature = token.split(".")
    flipped = ("a" if signature[0] != "a" else "b") + signature[1:]
    return f"{header}.{payload}.{flipped}"
