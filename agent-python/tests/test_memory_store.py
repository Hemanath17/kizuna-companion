import uuid

import pytest

import memory_store
from providers import EchoProvider

ORIGINAL_FACT = "I have a big interview Friday at Acme"
REWORDED_FACT = "I have a big interview on Friday at Acme Corp"
UNRELATED_FACT = "My favorite color is blue and I enjoy painting landscapes"

provider = EchoProvider()


async def _embed(text: str) -> list[float]:
    return await provider.embed(text)


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_store_and_search_roundtrip_returns_the_fact(pool, test_database):
    user_id = await _seed_user(test_database)

    await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content=ORIGINAL_FACT,
        embedding=await _embed(ORIGINAL_FACT),
        confidence=0.9,
    )

    results = await memory_store.search_memory(pool, user_id, await _embed(ORIGINAL_FACT), top_k=5)

    assert len(results) == 1
    assert results[0]["content"] == ORIGINAL_FACT
    assert results[0]["status"] == "active"


@pytest.mark.asyncio
async def test_cross_user_isolation(pool, test_database):
    user_a = await _seed_user(test_database)
    user_b = await _seed_user(test_database)

    memory_id = await memory_store.insert_memory(
        pool,
        user_a,
        memory_type="profile",
        content=ORIGINAL_FACT,
        embedding=await _embed(ORIGINAL_FACT),
    )

    # B can't see A's memory via search or list.
    assert await memory_store.search_memory(pool, user_b, await _embed(ORIGINAL_FACT)) == []
    assert await memory_store.list_memories(pool, user_b) == []

    # B can't mutate A's memory: RLS scopes the UPDATE to 0 rows, A's row is untouched.
    await memory_store.set_user_verified(pool, user_b, memory_id, True)
    await memory_store.supersede_memory(pool, user_b, memory_id, str(uuid.uuid4()))
    await memory_store.tombstone_memory(pool, user_b, memory_id)

    a_memories = await memory_store.list_memories(pool, user_a)
    assert len(a_memories) == 1
    assert a_memories[0]["status"] == "active"
    assert a_memories[0]["user_verified"] is False
    assert a_memories[0]["superseded_by"] is None


@pytest.mark.asyncio
async def test_supersede_preserves_provenance(pool, test_database):
    user_id = await _seed_user(test_database)

    old_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content=ORIGINAL_FACT,
        embedding=await _embed(ORIGINAL_FACT),
    )
    new_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content=REWORDED_FACT,
        embedding=await _embed(REWORDED_FACT),
    )

    await memory_store.supersede_memory(pool, user_id, old_id, new_id)

    memories = {m["id"]: m for m in await memory_store.list_memories(pool, user_id)}
    assert memories[uuid.UUID(old_id)]["status"] == "superseded"
    assert memories[uuid.UUID(old_id)]["superseded_by"] == uuid.UUID(new_id)
    assert memories[uuid.UUID(new_id)]["status"] == "active"

    # default search only considers status='active', so the superseded row
    # is excluded even though its content is still the closer embedding match.
    results = await memory_store.search_memory(pool, user_id, await _embed(ORIGINAL_FACT))
    assert all(r["id"] != uuid.UUID(old_id) for r in results)


@pytest.mark.asyncio
async def test_source_kind_defaults_to_stated_and_can_be_filtered(pool, test_database):
    user_id = await _seed_user(test_database)

    stated_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content=ORIGINAL_FACT,
        embedding=await _embed(ORIGINAL_FACT),
    )
    inferred_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content=REWORDED_FACT,
        embedding=await _embed(REWORDED_FACT),
        source_kind="inferred",
    )

    memories = {m["id"]: m for m in await memory_store.list_memories(pool, user_id)}
    assert memories[uuid.UUID(stated_id)]["source_kind"] == "stated"
    assert memories[uuid.UUID(inferred_id)]["source_kind"] == "inferred"

    inferred_only = await memory_store.search_memory(
        pool, user_id, await _embed(ORIGINAL_FACT), filters={"source_kind": "inferred"}
    )
    assert [r["id"] for r in inferred_only] == [uuid.UUID(inferred_id)]


@pytest.mark.asyncio
async def test_tombstone_check_matches_exact_and_reworded_repeat(pool, test_database):
    user_id = await _seed_user(test_database)

    memory_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="episodic",
        content=ORIGINAL_FACT,
        embedding=await _embed(ORIGINAL_FACT),
    )
    await memory_store.tombstone_memory(pool, user_id, memory_id)

    exact_match = await memory_store.check_tombstone(pool, user_id, ORIGINAL_FACT, await _embed(ORIGINAL_FACT))
    reworded_match = await memory_store.check_tombstone(pool, user_id, REWORDED_FACT, await _embed(REWORDED_FACT))
    no_match = await memory_store.check_tombstone(pool, user_id, UNRELATED_FACT, await _embed(UNRELATED_FACT))

    assert exact_match is True
    assert reworded_match is True
    assert no_match is False

    # Forgetting also removed the live memory and its embedding.
    memories = await memory_store.list_memories(pool, user_id)
    assert memories[0]["status"] == "deleted"
