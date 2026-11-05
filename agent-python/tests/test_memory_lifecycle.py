"""Phase 4 Step 4.1 Part A: user-initiated memory lifecycle operations
(policy.correct_memory / approve_memory / forget_memory), which back the new
/memory PATCH and DELETE endpoints. These prove the original .NET bug is
gone: a correction now produces a fresh, distinct embedding, and retrieval
for the corrected wording finds it -- the old wording never does.
"""

import uuid

import pytest

import memory_store
import policy
from context_builder import select_memories
from embeddings import embed_text
from providers import EchoProvider

provider = EchoProvider()

ORIGINAL = "has a big interview Friday at Acme"
CORRECTED = "has a big interview Thursday at Acme"


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_correct_memory_produces_fresh_distinct_embedding(pool, test_database):
    user_id = await _seed_user(test_database)
    embedding = await embed_text(provider, ORIGINAL)
    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=ORIGINAL, embedding=embedding
    )

    corrected = await policy.correct_memory(pool, user_id, provider, old_id, CORRECTED)
    assert corrected is not None
    assert corrected["content"] == CORRECTED
    assert corrected["user_verified"] is True
    assert corrected["source_kind"] == "stated"

    old = await memory_store.get_memory(pool, user_id, old_id)
    assert old["status"] == "superseded"
    assert str(old["superseded_by"]) == str(corrected["id"])

    # The bug this fixes: the old .NET path mutated content in place and
    # never touched the embedding column, so the row kept pointing at the
    # ORIGINAL wording. Here the two rows carry genuinely different vectors.
    rows = await test_database.superuser_fetch(
        "select id, embedding::text as embedding from memory where id = any($1::uuid[])", [old_id, corrected["id"]]
    )
    vectors = {str(row["id"]): row["embedding"] for row in rows}
    assert vectors[old_id] is not None
    assert vectors[str(corrected["id"])] is not None
    assert vectors[old_id] != vectors[str(corrected["id"])]


@pytest.mark.asyncio
async def test_correction_sticks_in_retrieval_and_the_old_wording_never_resurfaces(pool, test_database):
    user_id = await _seed_user(test_database)
    embedding = await embed_text(provider, ORIGINAL)
    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=ORIGINAL, embedding=embedding
    )

    await policy.correct_memory(pool, user_id, provider, old_id, CORRECTED)

    retrieved = await select_memories(pool, user_id, provider, "when's my interview again, Friday or what?")
    assert any("Thursday" in m["content"] and "Acme" in m["content"] for m in retrieved)
    assert not any("Friday" in m["content"] for m in retrieved)


@pytest.mark.asyncio
async def test_correct_memory_rejects_another_users_memory_id(pool, test_database):
    owner = await _seed_user(test_database)
    attacker = await _seed_user(test_database)
    embedding = await embed_text(provider, ORIGINAL)
    memory_id = await memory_store.insert_memory(
        pool, owner, memory_type="episodic", content=ORIGINAL, embedding=embedding
    )

    result = await policy.correct_memory(pool, attacker, provider, memory_id, "tampered")
    assert result is None

    untouched = await memory_store.get_memory(pool, owner, memory_id)
    assert untouched["content"] == ORIGINAL
    assert untouched["status"] == "active"


@pytest.mark.asyncio
async def test_approve_memory_sets_verified_without_changing_content_or_embedding(pool, test_database):
    user_id = await _seed_user(test_database)
    embedding = await embed_text(provider, "has a chronic illness")
    memory_id = await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content="has a chronic illness", embedding=embedding,
        is_sensitive=True, pending_confirmation=True,
    )

    approved = await policy.approve_memory(pool, user_id, memory_id)
    assert approved["content"] == "has a chronic illness"
    assert approved["user_verified"] is True
    assert approved["pending_confirmation"] is False


@pytest.mark.asyncio
async def test_forget_memory_tombstones_and_blocks_resurrection(pool, test_database):
    user_id = await _seed_user(test_database)
    embedding = await embed_text(provider, ORIGINAL)
    memory_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=ORIGINAL, embedding=embedding
    )

    assert await policy.forget_memory(pool, user_id, memory_id) is True
    forgotten = await memory_store.get_memory(pool, user_id, memory_id)
    assert forgotten["status"] == "deleted"

    # Already forgotten (or never existed) -> False, not a silent no-op success.
    assert await policy.forget_memory(pool, user_id, memory_id) is False
    assert await policy.forget_memory(pool, user_id, str(uuid.uuid4())) is False

    assert await memory_store.check_tombstone(pool, user_id, ORIGINAL, embedding) is True
