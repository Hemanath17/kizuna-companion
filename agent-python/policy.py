"""The policy layer: deterministic, no LLM. Decides what the memory agent's
PROPOSED operations actually get committed (PLAN.md sections 6, 16;
ARCHITECTURE.md 5.5). This is where the guarantees live -- the agent can
propose anything; this module is what can refuse or transform it.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

import memory_store
from embeddings import embed_text
from memory_agent import ProposedOperation
from providers import ProviderInterface

logger = logging.getLogger("kizuna.policy")

# How similar a NEW/EPISODIC_APPEND candidate must be to an already-active
# memory of the same type before it's treated as the same fact restated,
# not a second, duplicate row. Deliberately high: only a near-exact repeat
# is deduped here, not merely a related fact.
DEDUP_SIMILARITY_THRESHOLD = 0.95


@dataclass
class PolicyDecision:
    accepted: bool
    memory_id: str | None
    reason: str


async def commit_proposed_operations(
    pool: asyncpg.Pool,
    user_id: str,
    embed_provider: ProviderInterface,
    proposals: list[ProposedOperation],
    *,
    turn_timestamp: datetime,
    source_message_id: str | None = None,
    source_journal_entry_id: str | None = None,
) -> list[PolicyDecision]:
    """turn_timestamp must be timezone-aware: it's compared against stored
    timestamptz columns for the stale-job guard (rule 6). source_message_id /
    source_journal_entry_id identify which channel this whole batch of
    proposals came from (mutually exclusive, matching the DB's own check
    constraint) -- every row committed from this call carries it."""
    return [
        await _commit_one(
            pool, user_id, embed_provider, proposal, turn_timestamp, source_message_id, source_journal_entry_id
        )
        for proposal in proposals
    ]


async def _commit_one(
    pool: asyncpg.Pool,
    user_id: str,
    embed_provider: ProviderInterface,
    proposal: ProposedOperation,
    turn_timestamp: datetime,
    source_message_id: str | None,
    source_journal_entry_id: str | None,
) -> PolicyDecision:
    if proposal.op == "NOISE":
        return PolicyDecision(False, None, "noise")

    op = proposal.op
    # Rule 3: a changed feeling/state is an append, never an overwrite.
    # memory_type is the deterministic signal: episodic rows ARE the
    # time-bound feelings/events this rule protects ("loved it Monday, hated
    # it Friday"); profile rows are the stable facts a genuine SUPERSEDE is
    # for. Since EPISODIC_APPEND never touches the old row, the target
    # checks below don't apply to it.
    if op == "SUPERSEDE" and proposal.memory_type == "episodic":
        op = "EPISODIC_APPEND"
        logger.info("policy_transform op=SUPERSEDE->EPISODIC_APPEND reason=episodic_state_change")

    if op in ("UPDATE", "SUPERSEDE"):
        if proposal.target_id is None:
            return PolicyDecision(False, None, f"{op.lower()}_missing_target_id")
        target = await memory_store.get_memory(pool, user_id, proposal.target_id)
        if target is None:
            return PolicyDecision(False, None, "target_not_found")
        # Rule 1: never overwrite a user-verified memory on model disagreement.
        if target["user_verified"]:
            return PolicyDecision(False, None, "user_verified_cannot_be_overwritten")
        # Rule 6: a stale job can't undo a correction newer than itself.
        if target["updated_at"] > turn_timestamp:
            return PolicyDecision(False, None, "stale_job_superseded_by_newer_correction")

    embedding = await embed_text(embed_provider, proposal.content)

    # Rule 2: a forgotten fact cannot be resurrected, exact or reworded.
    if await memory_store.check_tombstone(pool, user_id, proposal.content, embedding):
        return PolicyDecision(False, None, "tombstoned")

    if op in ("NEW", "EPISODIC_APPEND"):
        existing = await memory_store.search_memory(
            pool, user_id, embedding, top_k=1, filters={"memory_type": proposal.memory_type}
        )
        if existing and existing[0]["similarity"] >= DEDUP_SIMILARITY_THRESHOLD:
            return PolicyDecision(False, str(existing[0]["id"]), "duplicate_of_existing_memory")

    # Rule 4: sensitive facts are held for explicit user confirmation --
    # forced here regardless of what the agent set, so it can't be forgotten.
    pending_confirmation = proposal.is_sensitive

    if op in ("NEW", "EPISODIC_APPEND"):
        memory_id = await memory_store.insert_memory(
            pool,
            user_id,
            memory_type=proposal.memory_type,
            content=proposal.content,
            embedding=embedding,
            confidence=proposal.confidence,
            is_sensitive=proposal.is_sensitive,
            pending_confirmation=pending_confirmation,
            source_kind=proposal.source_kind,  # Rule 7: never coerced to 'stated'.
            source_message_id=source_message_id,
            source_journal_entry_id=source_journal_entry_id,
        )
        return PolicyDecision(True, memory_id, f"committed_{op.lower()}")

    if op in ("UPDATE", "SUPERSEDE"):
        # Rule 5: a correction is a NEW row that supersedes the old one --
        # provenance (superseded_by, full history) is preserved; the old row
        # is never mutated in place or hard-deleted.
        memory_id = await memory_store.insert_memory(
            pool,
            user_id,
            memory_type=proposal.memory_type,
            content=proposal.content,
            embedding=embedding,
            confidence=proposal.confidence,
            is_sensitive=proposal.is_sensitive,
            pending_confirmation=pending_confirmation,
            source_kind=proposal.source_kind,
            source_message_id=source_message_id,
            source_journal_entry_id=source_journal_entry_id,
        )
        await memory_store.supersede_memory(pool, user_id, proposal.target_id, memory_id)
        return PolicyDecision(True, memory_id, f"committed_{op.lower()}_with_provenance")

    return PolicyDecision(False, None, f"unknown_op:{op}")


# --- User-initiated lifecycle operations (Phase 4 decision 1/2) ---------
#
# These back the /memory PATCH and DELETE endpoints. They reuse the same
# memory_store primitives _commit_one uses (insert_memory, supersede_memory,
# tombstone_memory) rather than a second implementation, but deliberately
# skip _commit_one's agent-disagreement guards (rule 1's user_verified check,
# rule 6's stale-job timestamp guard): those protect a verified fact from
# being overwritten by a MODEL that disagrees, not from the same user
# correcting their own fact again.


async def correct_memory(
    pool: asyncpg.Pool, user_id: str, embed_provider: ProviderInterface, memory_id: str, new_content: str
) -> dict | None:
    """Supersede memory_id with a new row carrying a freshly computed
    embedding, user_verified=true, source_kind='stated'. Returns the new
    row, or None if memory_id doesn't exist / isn't owned by user_id."""
    target = await memory_store.get_memory(pool, user_id, memory_id)
    if target is None:
        return None
    embedding = await embed_text(embed_provider, new_content)
    new_id = await memory_store.insert_memory(
        pool,
        user_id,
        memory_type=target["memory_type"],
        content=new_content,
        embedding=embedding,
        confidence=target["confidence"],
        is_sensitive=target["is_sensitive"],
        source_kind="stated",
        # Carried forward from the row being corrected, not re-derived: a
        # correction to a journal-sourced memory stays distinguishable as
        # journal-sourced (and likewise for chat), matching PLAN.md section 6
        # (provenance is preserved across a supersede, not just the chain).
        source_message_id=target["source_message_id"],
        source_journal_entry_id=target["source_journal_entry_id"],
    )
    await memory_store.set_user_verified(pool, user_id, new_id, True)
    await memory_store.supersede_memory(pool, user_id, memory_id, new_id)
    return await memory_store.get_memory(pool, user_id, new_id)


async def approve_memory(pool: asyncpg.Pool, user_id: str, memory_id: str) -> dict | None:
    """A bare approval (no content change): confirms a pending/unverified
    memory in place. No new row and no re-embedding, since the content
    didn't change."""
    target = await memory_store.get_memory(pool, user_id, memory_id)
    if target is None:
        return None
    await memory_store.set_user_verified(pool, user_id, memory_id, True)
    return await memory_store.get_memory(pool, user_id, memory_id)


async def forget_memory(pool: asyncpg.Pool, user_id: str, memory_id: str) -> bool:
    """Tombstones memory_id (content_hash + embedding) and marks it deleted,
    exactly as the Phase 3 extractor path does. Returns False if memory_id
    doesn't exist, isn't owned by user_id, or was already forgotten."""
    target = await memory_store.get_memory(pool, user_id, memory_id)
    if target is None or target["status"] == "deleted":
        return False
    await memory_store.tombstone_memory(pool, user_id, memory_id)
    return True
