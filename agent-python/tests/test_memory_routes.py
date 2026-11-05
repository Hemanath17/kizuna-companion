"""Phase 4 Step 4.1 Part A: the Python-side /memory endpoints the .NET
proxy forwards to. Exercises the real HTTP surface (auth scope, status
codes), not just the policy.py functions underneath -- test_memory_lifecycle.py
covers those directly.
"""

import uuid

import httpx
import pytest

import memory_store
from embeddings import embed_text
from providers import EchoProvider
from tokens import mint_token

provider = EchoProvider()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _memory_token(user_id: str) -> str:
    return mint_token(user_id=user_id, turn_id=str(uuid.uuid4()), scope="memory:manage")


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_list_patch_delete_round_trip(app, pool, test_database):
    user_id = await _seed_user(test_database)
    embedding = await embed_text(provider, "works as a data engineer at Acme")
    memory_id = await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content="works as a data engineer at Acme", embedding=embedding
    )
    token = _memory_token(user_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/memory", headers=_auth_header(token))
        assert listed.status_code == 200
        assert [m["id"] for m in listed.json()] == [memory_id]

        patched = await client.patch(
            f"/memory/{memory_id}",
            json={"content": "works as a senior data engineer at Acme"},
            headers=_auth_header(token),
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["content"] == "works as a senior data engineer at Acme"
        assert body["user_verified"] is True
        new_id = body["id"]

        deleted = await client.delete(f"/memory/{new_id}", headers=_auth_header(token))
        assert deleted.status_code == 204

        listed_after = await client.get("/memory", headers=_auth_header(token))
        assert listed_after.json() == []


@pytest.mark.asyncio
async def test_chat_generate_token_cannot_call_memory_endpoints(app, pool, test_database):
    user_id = await _seed_user(test_database)
    wrong_scope_token = mint_token(user_id=user_id, turn_id=str(uuid.uuid4()), scope="chat:generate")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/memory", headers=_auth_header(wrong_scope_token))
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_patch_nonexistent_memory_returns_404_not_500(app, pool, test_database):
    user_id = await _seed_user(test_database)
    token = _memory_token(user_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/memory/{uuid.uuid4()}", json={"content": "doesn't matter"}, headers=_auth_header(token)
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_patch_malformed_id_returns_404(app, pool, test_database):
    user_id = await _seed_user(test_database)
    token = _memory_token(user_id)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/memory/not-a-uuid", json={"content": "doesn't matter"}, headers=_auth_header(token)
        )
    assert response.status_code == 404
