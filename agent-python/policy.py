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
) -> list[PolicyDecision]:
    """turn_timestamp must be timezone-aware: it's compared against stored
    timestamptz columns for the stale-job guard (rule 6)."""
    return [await _commit_one(pool, user_id, embed_provider, proposal, turn_timestamp) for proposal in proposals]


async def _commit_one(
    pool: asyncpg.Pool,
    user_id: str,
    embed_provider: ProviderInterface,
    proposal: ProposedOperation,
    turn_timestamp: datetime,
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
        )
        await memory_store.supersede_memory(pool, user_id, proposal.target_id, memory_id)
        return PolicyDecision(True, memory_id, f"committed_{op.lower()}_with_provenance")

    return PolicyDecision(False, None, f"unknown_op:{op}")
