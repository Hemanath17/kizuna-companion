import uuid

import httpx
import pytest
from fastapi import FastAPI

from chat import router as chat_router
from fake_openai import FakeAsyncOpenAIClient
from generation import SAFE_FALLBACK_MESSAGE
from providers import OpenAIProvider
from tokens import AUDIENCE, ISSUER, SIGNING_KEY, mint_token


@pytest.mark.asyncio
async def test_provider_error_falls_back_but_turn_is_still_persisted(pool, test_database, monkeypatch):
    monkeypatch.setenv("INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("INTERNAL_TOKEN_AUDIENCE", AUDIENCE)

    fake_client = FakeAsyncOpenAIClient(raise_on_create=RuntimeError("provider is down"))
    app = FastAPI()
    app.include_router(chat_router)
    app.state.pool = pool
    app.state.provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")

    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    await test_database.seed_user_message(user_id, conversation_id, turn_id, "hello there", sequence_number=0)

    token = mint_token(user_id=str(user_id), turn_id=str(turn_id))
    body = {
        "conversation_id": str(conversation_id),
        "turn_id": str(turn_id),
        "text": "hello there",
        "sequence_number": 0,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat/stream", json=body, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert "event: done" in response.text
    assert "event: error" not in response.text  # degraded, not failed

    messages = await test_database.fetch_messages(conversation_id)
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["status"] == "complete"
    assert assistant_messages[0]["content"].strip() == SAFE_FALLBACK_MESSAGE

    outbox_rows = await test_database.fetch_outbox(turn_id)
    assert len(outbox_rows) == 1
