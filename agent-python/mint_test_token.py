#!/usr/bin/env python3
"""Mints a short-lived internal token for manually testing /chat/stream.

Usage: python mint_test_token.py [user_id] [turn_id]
Both args default to a fresh random UUID if omitted.
"""
import os
import sys
import time
import uuid

import jwt
from dotenv import load_dotenv

from auth import CHAT_GENERATE_SCOPE

load_dotenv()


def main():
    signing_key = os.environ.get("INTERNAL_SIGNING_KEY")
    issuer = os.environ.get("INTERNAL_TOKEN_ISSUER")
    audience = os.environ.get("INTERNAL_TOKEN_AUDIENCE")
    missing = [
        name
        for name, value in [
            ("INTERNAL_SIGNING_KEY", signing_key),
            ("INTERNAL_TOKEN_ISSUER", issuer),
            ("INTERNAL_TOKEN_AUDIENCE", audience),
        ]
        if not value
    ]
    if missing:
        sys.exit(f"Missing required .env values: {', '.join(missing)}")

    user_id = sys.argv[1] if len(sys.argv) > 1 else str(uuid.uuid4())
    turn_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4())
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": issuer,
            "aud": audience,
            "sub": user_id,
            "scope": CHAT_GENERATE_SCOPE,
            "jti": str(uuid.uuid4()),
            "iat": now,
            "exp": now + 300,
            "turn_id": turn_id,
        },
        signing_key,
        algorithm="HS256",
    )
    print(f"user_id: {user_id}")
    print(f"turn_id: {turn_id}")
    print(f"token:   {token}")


if __name__ == "__main__":
    main()
