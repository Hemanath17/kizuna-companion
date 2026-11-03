import uuid
from datetime import datetime, timedelta, timezone

import pytest

import memory_store
from context_builder import (
    MEMORY_COUNT_CAP,
    format_memory_block,
    select_memories,
)
from providers import EchoProvider

provider = EchoProvider()


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


async def _embed(text: str) -> list[float]:
    return await provider.embed(text)


@pytest.mark.asyncio
async def test_pending_confirmation_memories_are_excluded(pool, test_database):
    user_id = await _seed_user(test_database)
    await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content="has a chronic illness",
        embedding=await _embed("has a chronic illness"),
        is_sensitive=True,
        pending_confirmation=True,
    )

    results = await select_memories(pool, user_id, provider, "chronic illness")

    assert results == []


@pytest.mark.asyncio
async def test_user_verified_memory_outranks_a_slightly_more_similar_unverified_one(pool, test_database):
    user_id = await _seed_user(test_database)
    query = "I work as a data engineer at Acme"

    # Slightly less similar to the query, but user-verified.
    verified_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="profile",
        content="works as a data engineer",
        embedding=await _embed("works as a data engineer"),
    )
    await memory_store.set_user_verified(pool, user_id, verified_id, True)

    # Slightly more similar to the query, but not verified -- the verified
    # boost (0.2) is bigger than this small a similarity gap (~0.023 after
    # weighting), so verification should still win the ranking.
    await memory_store.insert_memory(
        pool,
        user_id,
        memory_type="episodic",
        content="works as a data engineer at Acme in the city",
        embedding=await _embed("works as a data engineer at Acme in the city"),
    )

    results = await select_memories(pool, user_id, provider, query)

    assert results[0]["id"] == uuid.UUID(verified_id)


@pytest.mark.asyncio
async def test_recency_breaks_ties_between_equally_similar_memories(pool, test_database):
    user_id = await _seed_user(test_database)
    content = "acme interview friday"

    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=content, embedding=await _embed(content)
    )
    new_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=content, embedding=await _embed(content)
    )
    await test_database.superuser_fetch(
        "update memory set created_at = $1 where id = $2",
        datetime.now(timezone.utc) - timedelta(days=90),
        uuid.UUID(old_id),
    )

    results = await select_memories(pool, user_id, provider, content)

    assert [r["id"] for r in results] == [uuid.UUID(new_id), uuid.UUID(old_id)]


@pytest.mark.asyncio
async def test_count_cap_limits_how_many_memories_are_selected(pool, test_database):
    user_id = await _seed_user(test_database)
    for i in range(MEMORY_COUNT_CAP + 3):
        content = f"fact number {i} about acme interview friday"
        await memory_store.insert_memory(
            pool, user_id, memory_type="profile", content=content, embedding=await _embed(content)
        )

    results = await select_memories(pool, user_id, provider, "acme interview friday")

    assert len(results) == MEMORY_COUNT_CAP


@pytest.mark.asyncio
async def test_token_budget_still_includes_at_least_one_oversized_memory(pool, test_database):
    user_id = await _seed_user(test_database)
    huge_content = "acme interview friday " + ("word " * 2000)
    await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content=huge_content, embedding=await _embed(huge_content)
    )

    results = await select_memories(pool, user_id, provider, "acme interview friday", token_budget=10)

    assert len(results) == 1


def test_format_memory_block_is_empty_for_no_memories():
    assert format_memory_block([]) == ""


def test_format_memory_block_lists_each_memorys_content():
    block = format_memory_block([{"content": "works at Acme"}, {"content": "likes hiking"}])
    assert "works at Acme" in block
    assert "likes hiking" in block
