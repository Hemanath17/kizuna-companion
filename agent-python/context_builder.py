"""Selects which of a user's memories enter the generation prompt
(ARCHITECTURE.md 5.8, PLAN.md section 17: "Context Builder... filters
authorized memory by permission, relevance, sensitivity, recency, token
budget before it enters the prompt").
"""

from datetime import datetime, timezone

import asyncpg

import memory_store
from embeddings import embed_text
from providers import ProviderInterface

# Vector similarity candidates pulled from the DB before blended ranking.
CANDIDATE_TOP_K = 8

# How many ranked memories can actually enter the prompt.
MEMORY_COUNT_CAP = 5

# A rough token budget for the formatted block, applied via a 4-chars/token
# approximation -- cheap and conservative; exact tokenization isn't worth
# it for a cap this small.
MEMORY_TOKEN_BUDGET = 400
_CHARS_PER_TOKEN = 4

# Blended ranking weights: similarity to THIS message dominates; recency is
# a secondary nudge; a user-verified fact gets a flat trust boost since it's
# confirmed ground truth, not a model guess. Not required to sum to 1 --
# this is a ranking score, not a probability.
SIMILARITY_WEIGHT = 0.6
RECENCY_WEIGHT = 0.2
VERIFIED_BOOST = 0.2
RECENCY_HALF_LIFE_DAYS = 30.0


def _recency_score(created_at: datetime, *, now: datetime) -> float:
    age_days = max((now - created_at).total_seconds() / 86400, 0.0)
    return 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)


def _blended_score(memory: dict, *, now: datetime) -> float:
    score = memory["similarity"] * SIMILARITY_WEIGHT
    score += _recency_score(memory["created_at"], now=now) * RECENCY_WEIGHT
    if memory["user_verified"]:
        score += VERIFIED_BOOST
    return score


async def select_memories(
    pool: asyncpg.Pool,
    user_id: str,
    embed_provider: ProviderInterface,
    query_text: str,
    *,
    count_cap: int = MEMORY_COUNT_CAP,
    token_budget: int = MEMORY_TOKEN_BUDGET,
    top_k: int = CANDIDATE_TOP_K,
) -> list[dict]:
    """Candidates come from memory_store.search_memory, which is already
    user-scoped (RLS) and status='active' by default; pending_confirmation
    is excluded explicitly here so a sensitive fact awaiting the user's own
    approval never leaks into a reply."""
    embedding = await embed_text(embed_provider, query_text)
    candidates = await memory_store.search_memory(
        pool, user_id, embedding, top_k, filters={"pending_confirmation": False}
    )

    now = datetime.now(timezone.utc)
    ranked = sorted(candidates, key=lambda m: _blended_score(m, now=now), reverse=True)

    char_budget = token_budget * _CHARS_PER_TOKEN
    selected: list[dict] = []
    used_chars = 0
    for memory in ranked:
        if len(selected) >= count_cap:
            break
        line_chars = len(memory["content"])
        # Always take at least one memory even if it alone exceeds the
        # budget; only STOP ADDING once something is already in.
        if selected and used_chars + line_chars > char_budget:
            break
        selected.append(memory)
        used_chars += line_chars
    return selected


def format_memory_block(memories: list[dict]) -> str:
    if not memories:
        return ""
    lines = [f"- {memory['content']}" for memory in memories]
    return "What you remember about this person:\n" + "\n".join(lines)
