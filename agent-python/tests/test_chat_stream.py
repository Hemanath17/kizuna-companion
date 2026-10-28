import uuid

import httpx
import pytest

from asgi_helpers import json_body, post_with_disconnect
from tokens import mint_token, tamper


def _auth_header(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


async def _seed(test_database):
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    # Simulates .NET's acceptance step, which now owns inserting this row.
    await test_database.seed_user_message(user_id, conversation_id, turn_id, "hello there", sequence_number=0)
    return user_id, conversation_id, turn_id


@pytest.mark.asyncio
async def test_valid_token_streams_reply_and_persists_assistant_and_outbox_atomically(app, test_database):
    user_id, conversation_id, turn_id = await _seed(test_database)

    token = mint_token(user_id=str(user_id), turn_id=str(turn_id))
    body = {
        "conversation_id": str(conversation_id),
        "turn_id": str(turn_id),
        "text": "hello there",
        "sequence_number": 0,
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat/stream", json=body, headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert "event: token" in response.text
    assert "event: done" in response.text

    messages = await test_database.fetch_messages(conversation_id)
    roles_and_status = [(m["role"], m["status"]) for m in messages]
    # The user row is the one the test pre-seeded (standing in for .NET); it
    # must be left untouched. Python must have added exactly one more row:
    # the assistant reply.
    assert roles_and_status.count(("user", "accepted")) == 1
    assert roles_and_status.count(("assistant", "complete")) == 1
    assert len(messages) == 2

    outbox_rows = await test_database.fetch_outbox(turn_id)
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["job_type"] == "process_memory"
    assert outbox_rows[0]["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "build_headers",
    [
        pytest.param(lambda user_id, turn_id: {}, id="missing_token"),
        pytest.param(
            lambda user_id, turn_id: {
                "Authorization": f"Bearer {mint_token(user_id=str(user_id), turn_id=str(turn_id), expires_in=-60)}"
            },
            id="expired_token",
        ),
        pytest.param(
            lambda user_id, turn_id: {
                "Authorization": "Bearer "
                + mint_token(user_id=str(user_id), turn_id=str(turn_id), audience="some-other-audience")
            },
            id="wrong_audience",
        ),
        pytest.param(
            lambda user_id, turn_id: {
                "Authorization": "Bearer "
                + mint_token(user_id=str(user_id), turn_id=str(turn_id), scope="memory:write")
            },
            id="wrong_scope",
        ),
        pytest.param(
            lambda user_id, turn_id: {
                "Authorization": "Bearer "
                + tamper(mint_token(user_id=str(user_id), turn_id=str(turn_id)))
            },
            id="tampered_token",
        ),
        pytest.param(
            # Token is otherwise valid but scoped to a different turn_id than
            # the body claims -- a token minted for one turn must not work for
            # another, even for the same user.
            lambda user_id, turn_id: {
                "Authorization": "Bearer "
                + mint_token(user_id=str(user_id), turn_id=str(uuid.uuid4()))
            },
            id="turn_id_mismatch",
        ),
    ],
)
async def test_bad_token_returns_401_and_persists_nothing_new(app, test_database, build_headers):
    user_id, conversation_id, turn_id = await _seed(test_database)

    body = {
        "conversation_id": str(conversation_id),
        "turn_id": str(turn_id),
        "text": "hello there",
        "sequence_number": 0,
    }
    headers = build_headers(user_id, turn_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat/stream", json=body, headers=headers)

    assert response.status_code == 401

    # Only the pre-seeded user row should exist; Python must not have touched
    # anything (no assistant row, no status change) when it rejects the token.
    messages = await test_database.fetch_messages(conversation_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["status"] == "accepted"
    outbox_rows = await test_database.fetch_outbox(turn_id)
    assert outbox_rows == []


@pytest.mark.asyncio
async def test_disconnect_after_token_events_marks_the_existing_user_message_interrupted(app, test_database):
    user_id, conversation_id, turn_id = await _seed(test_database)

    token = mint_token(user_id=str(user_id), turn_id=str(turn_id))
    body = {
        "conversation_id": str(conversation_id),
        "turn_id": str(turn_id),
        "text": "hello there, this is a longer message so there are several tokens to stream",
        "sequence_number": 0,
    }

    status_code, chunks = await post_with_disconnect(
        app,
        "/chat/stream",
        json_body(body),
        _auth_header(token),
        disconnect_after_sends=2,
    )

    assert status_code == 200
    # Disconnected after 2 chunks were sent, before the stream reached 'done'.
    assert all(b"event: done" not in c for c in chunks)

    messages = await test_database.fetch_messages(conversation_id)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["status"] == "interrupted"

    outbox_rows = await test_database.fetch_outbox(turn_id)
    assert outbox_rows == []
