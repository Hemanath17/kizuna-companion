import os

import jwt
from fastapi import Header, HTTPException

CHAT_GENERATE_SCOPE = "chat:generate"


class InternalClaims:
    def __init__(self, user_id: str, turn_id: str):
        self.user_id = user_id
        self.turn_id = turn_id


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def require_chat_generate(authorization: str | None = Header(default=None)) -> InternalClaims:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ")

    try:
        claims = jwt.decode(
            token,
            _require_env("INTERNAL_SIGNING_KEY"),
            algorithms=["HS256"],
            issuer=_require_env("INTERNAL_TOKEN_ISSUER"),
            audience=_require_env("INTERNAL_TOKEN_AUDIENCE"),
            options={"require": ["exp", "iss", "aud", "sub", "scope", "turn_id"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid internal token")

    if claims.get("scope") != CHAT_GENERATE_SCOPE:
        raise HTTPException(status_code=401, detail="Token missing required scope")

    return InternalClaims(user_id=claims["sub"], turn_id=claims["turn_id"])
