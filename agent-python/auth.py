import os

import jwt
from fastapi import Header, HTTPException

CHAT_GENERATE_SCOPE = "chat:generate"
MEMORY_MANAGE_SCOPE = "memory:manage"


class InternalClaims:
    def __init__(self, user_id: str, turn_id: str | None = None):
        self.user_id = user_id
        self.turn_id = turn_id


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _decode(token: str, *, required_claims: list[str], scope: str) -> dict:
    try:
        claims = jwt.decode(
            token,
            _require_env("INTERNAL_SIGNING_KEY"),
            algorithms=["HS256"],
            issuer=_require_env("INTERNAL_TOKEN_ISSUER"),
            audience=_require_env("INTERNAL_TOKEN_AUDIENCE"),
            options={"require": required_claims},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    if claims.get("scope") != scope:
        raise HTTPException(status_code=401, detail="Token missing required scope")
    return claims


async def require_chat_generate(authorization: str | None = Header(default=None)) -> InternalClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    claims = _decode(
        token, required_claims=["exp", "iss", "aud", "sub", "scope", "turn_id"], scope=CHAT_GENERATE_SCOPE
    )
    return InternalClaims(user_id=claims["sub"], turn_id=claims["turn_id"])


async def require_memory_manage(authorization: str | None = Header(default=None)) -> InternalClaims:
    # No turn_id: memory mutations (the user's own corrections/forgets) are
    # not tied to a chat turn, unlike chat:generate.
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    claims = _decode(token, required_claims=["exp", "iss", "aud", "sub", "scope"], scope=MEMORY_MANAGE_SCOPE)
    return InternalClaims(user_id=claims["sub"])
