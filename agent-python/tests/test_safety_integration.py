import uuid

import httpx
import pytest
from fastapi import FastAPI

from chat import router as chat_router
from fake_openai import FakeAsyncOpenAIClient, make_chunk
from providers import OpenAIProvider
from safety import SafetyAssessment
from tokens import AUDIENCE, ISSUER, SIGNING_KEY, mint_token


def _build_app(pool, fake_client):
    app = FastAPI()
    app.include_router(chat_router)
    app.state.pool = pool
    app.state.provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    return app


async def _seed(test_database, text: str):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    await test_database.seed_user_message(user_id, conversation_id, turn_id, text, sequence_number=0)
    return user_id, conversation_id, turn_id


async def _post_chat(app, user_id, conversation_id, turn_id, text):
    token = mint_token(user_id=str(user_id), turn_id=str(turn_id))
    body = {"conversation_id": str(conversation_id), "turn_id": str(turn_id), "text": text, "sequence_number": 0}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/chat/stream", json=body, headers={"Authorization": f"Bearer {token}"})


@pytest.mark.asyncio
async def test_crisis_message_gets_static_response_and_logs_crisis_event_without_calling_generation(
    pool, test_database, monkeypatch
):
    monkeypatch.setenv("INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("INTERNAL_TOKEN_AUDIENCE", AUDIENCE)

    def parse_fn(kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "Return flagged" in prompt:
            from safety import Stage1Signal

            return Stage1Signal(flagged=True, categories=["self_harm"], ambiguity=False, context_shift=True), None
        return (
            SafetyAssessment(
                risk_category="self_harm", severity="high", confidence=0.95, uncertainty=0.05,
                recommended_policy="crisis", reason="explicit statement of intent to end their life",
            ),
            None,
        )

    fake_client = FakeAsyncOpenAIClient(parse_fn=parse_fn)
    app = _build_app(pool, fake_client)

    user_id, conversation_id, turn_id = await _seed(test_database, "I want to kill myself")
    response = await _post_chat(app, user_id, conversation_id, turn_id, "I want to kill myself")

    assert response.status_code == 200
    assert "event: done" in response.text
    # Each word is its own SSE token frame, so check single tokens, not a
    # multi-word phrase (which would never appear contiguous in the raw text).
    assert '"text": "988 "' in response.text  # the static crisis text streamed over the normal SSE channel
    assert fake_client.create_calls == []  # generation LLM never called

    crisis_rows = await test_database.superuser_fetch(
        "select risk_category, severity, policy_outcome from crisis_events where user_id = $1", user_id
    )
    assert len(crisis_rows) == 1
    assert crisis_rows[0]["risk_category"] == "self_harm"
    assert crisis_rows[0]["policy_outcome"] == "crisis"

    messages = await test_database.fetch_messages(conversation_id)
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert "Call or text 988" in assistant_messages[0]["content"]
    assert assistant_messages[0]["status"] == "complete"


@pytest.mark.asyncio
async def test_ordinary_bad_day_is_not_escalated_and_logs_no_crisis_event(pool, test_database, monkeypatch):
    monkeypatch.setenv("INTERNAL_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_TOKEN_ISSUER", ISSUER)
    monkeypatch.setenv("INTERNAL_TOKEN_AUDIENCE", AUDIENCE)

    fake_client = FakeAsyncOpenAIClient(stream_chunks=[make_chunk(content="That sounds exhausting, want to vent?")])
    app = _build_app(pool, fake_client)

    text = "rough week at work, I'm exhausted and just need to vent"
    user_id, conversation_id, turn_id = await _seed(test_database, text)
    response = await _post_chat(app, user_id, conversation_id, turn_id, text)

    assert response.status_code == 200
    assert "event: done" in response.text
    assert fake_client.parse_calls == []  # Stage 1's cheap pre-filter skipped the call for an ordinary message

    crisis_rows = await test_database.superuser_fetch(
        "select 1 from crisis_events where user_id = $1", user_id
    )
    assert crisis_rows == []

    messages = await test_database.fetch_messages(conversation_id)
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0]["status"] == "complete"
