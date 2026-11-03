import uuid
from datetime import datetime, timedelta, timezone

import pytest

import memory_store
import policy
from memory_agent import ProposedOperation
from providers import EchoProvider

provider = EchoProvider()


def _op(**overrides) -> ProposedOperation:
    base = dict(
        op="NEW",
        target_id=None,
        content="works as a data engineer",
        memory_type="profile",
        confidence=0.9,
        is_sensitive=False,
        source_kind="stated",
        reason="user said so",
    )
    base.update(overrides)
    return ProposedOperation(**base)


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


async def _embed(text: str) -> list[float]:
    return await provider.embed(text)


async def _commit(pool, user_id, proposals, turn_timestamp=None):
    # Computed at call time, not import time: it's compared against
    # updated_at values the DB stamps with its own now() during the test,
    # which must be allowed to be a genuine update's trigger, not "stale".
    turn_timestamp = turn_timestamp or datetime.now(timezone.utc)
    return await policy.commit_proposed_operations(pool, user_id, provider, proposals, turn_timestamp=turn_timestamp)


@pytest.mark.asyncio
async def test_rule1_never_overwrites_a_user_verified_memory(pool, test_database):
    user_id = await _seed_user(test_database)
    memory_id = await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content="works at Acme", embedding=await _embed("works at Acme")
    )
    await memory_store.set_user_verified(pool, user_id, memory_id, True)

    decisions = await _commit(
        pool, user_id, [_op(op="UPDATE", target_id=memory_id, content="works at Globex, the model thinks")]
    )

    assert decisions[0].accepted is False
    assert decisions[0].reason == "user_verified_cannot_be_overwritten"
    row = await memory_store.get_memory(pool, user_id, memory_id)
    assert row["content"] == "works at Acme"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_rule2_tombstoned_fact_cannot_be_resurrected(pool, test_database):
    user_id = await _seed_user(test_database)
    content = "has a big interview Friday at Acme"
    memory_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content=content, embedding=await _embed(content)
    )
    await memory_store.tombstone_memory(pool, user_id, memory_id)

    decisions = await _commit(pool, user_id, [_op(op="NEW", memory_type="episodic", content=content)])

    assert decisions[0].accepted is False
    assert decisions[0].reason == "tombstoned"
    memories = await memory_store.list_memories(pool, user_id)
    assert len(memories) == 1  # the tombstoned original only; nothing new was inserted
    assert memories[0]["status"] == "deleted"


@pytest.mark.asyncio
async def test_rule3_changed_feeling_is_episodic_append_not_supersede(pool, test_database):
    user_id = await _seed_user(test_database)
    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="episodic", content="loved the job Monday", embedding=await _embed("loved the job Monday")
    )

    decisions = await _commit(
        pool,
        user_id,
        [_op(op="SUPERSEDE", target_id=old_id, memory_type="episodic", content="hated the job Friday")],
    )

    assert decisions[0].accepted is True
    assert decisions[0].reason == "committed_episodic_append"
    old_row = await memory_store.get_memory(pool, user_id, old_id)
    assert old_row["status"] == "active"
    assert old_row["superseded_by"] is None
    new_row = await memory_store.get_memory(pool, user_id, decisions[0].memory_id)
    assert new_row["content"] == "hated the job Friday"
    assert new_row["status"] == "active"


@pytest.mark.asyncio
async def test_rule4_sensitive_fact_is_held_pending_confirmation(pool, test_database):
    user_id = await _seed_user(test_database)

    decisions = await _commit(
        pool, user_id, [_op(content="was diagnosed with a chronic illness", is_sensitive=True)]
    )

    assert decisions[0].accepted is True
    row = await memory_store.get_memory(pool, user_id, decisions[0].memory_id)
    assert row["is_sensitive"] is True
    assert row["pending_confirmation"] is True
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_rule5_genuine_update_supersedes_with_provenance_intact(pool, test_database):
    user_id = await _seed_user(test_database)
    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content="works at Acme", embedding=await _embed("works at Acme")
    )

    decisions = await _commit(pool, user_id, [_op(op="UPDATE", target_id=old_id, content="works at Globex now")])

    assert decisions[0].accepted is True
    assert decisions[0].reason == "committed_update_with_provenance"
    old_row = await memory_store.get_memory(pool, user_id, old_id)
    assert old_row["status"] == "superseded"
    assert str(old_row["superseded_by"]) == decisions[0].memory_id
    new_row = await memory_store.get_memory(pool, user_id, decisions[0].memory_id)
    assert new_row["status"] == "active"
    assert new_row["content"] == "works at Globex now"


@pytest.mark.asyncio
async def test_rule6_stale_job_cannot_undo_a_newer_correction(pool, test_database):
    user_id = await _seed_user(test_database)
    old_id = await memory_store.insert_memory(
        pool, user_id, memory_type="profile", content="works at Acme", embedding=await _embed("works at Acme")
    )
    # The memory's updated_at is ~now; a job stamped with an older turn
    # timestamp must not be allowed to override a correction newer than itself.
    stale_turn_timestamp = datetime.now(timezone.utc) - timedelta(days=1)

    decisions = await _commit(
        pool,
        user_id,
        [_op(op="UPDATE", target_id=old_id, content="works at Globex (stale)")],
        turn_timestamp=stale_turn_timestamp,
    )

    assert decisions[0].accepted is False
    assert decisions[0].reason == "stale_job_superseded_by_newer_correction"
    row = await memory_store.get_memory(pool, user_id, old_id)
    assert row["content"] == "works at Acme"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_rule7_inferred_fact_is_never_coerced_to_stated(pool, test_database):
    user_id = await _seed_user(test_database)

    decisions = await _commit(
        pool, user_id, [_op(content="seems stressed about work lately", source_kind="inferred", memory_type="episodic")]
    )

    assert decisions[0].accepted is True
    row = await memory_store.get_memory(pool, user_id, decisions[0].memory_id)
    assert row["source_kind"] == "inferred"


@pytest.mark.asyncio
async def test_repeated_fact_does_not_duplicate(pool, test_database):
    user_id = await _seed_user(test_database)
    content = "works as a data engineer"
    first_decisions = await _commit(pool, user_id, [_op(content=content)])
    assert first_decisions[0].accepted is True

    second_decisions = await _commit(pool, user_id, [_op(content=content)])

    assert second_decisions[0].accepted is False
    assert second_decisions[0].reason == "duplicate_of_existing_memory"
    memories = await memory_store.list_memories(pool, user_id)
    assert len([m for m in memories if m["content"] == content]) == 1


@pytest.mark.asyncio
async def test_noise_proposals_are_dropped(pool, test_database):
    user_id = await _seed_user(test_database)

    decisions = await _commit(pool, user_id, [_op(op="NOISE", content="lol")])

    assert decisions[0].accepted is False
    assert decisions[0].reason == "noise"
    assert await memory_store.list_memories(pool, user_id) == []
