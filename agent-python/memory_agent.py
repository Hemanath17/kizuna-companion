"""The memory agent: PROPOSES memory operations, never writes them
(ARCHITECTURE.md 5.5, PLAN.md sections 6, 2b, 16). A separate, deterministic
policy layer (policy.py) decides what actually gets committed -- this module
has no DB write access at all, only the two read-only tools below.
"""

import os

# Must be set before pydantic_ai renders its startup banner (on first Agent
# use), not just before importing it.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dataclasses import dataclass  # noqa: E402
from typing import Literal  # noqa: E402

import asyncpg  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from pydantic_ai import Agent, RunContext, UsageLimits  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

import memory_store  # noqa: E402
from embeddings import embed_text  # noqa: E402
from providers import ProviderInterface  # noqa: E402

# Generous enough for a few search_memory calls across several candidate
# facts in one turn plus a get_recent_memories pull -- real multi-step
# latitude -- but bounded so one turn can never trigger unbounded model
# round-trips. Enforced by pydantic_ai's UsageLimits, not hand-rolled
# counting.
TOOL_CALL_BUDGET = 6

# Token-discipline cap on the agent's own output (a handful of short
# proposed operations, not prose).
MAX_OUTPUT_TOKENS = 600


class ProposedOperation(BaseModel):
    op: Literal["NEW", "UPDATE", "SUPERSEDE", "EPISODIC_APPEND", "NOISE"]
    target_id: str | None = None
    content: str
    memory_type: Literal["profile", "episodic"]
    confidence: float
    is_sensitive: bool
    source_kind: Literal["stated", "inferred"]
    reason: str


@dataclass
class MemoryAgentDeps:
    pool: asyncpg.Pool
    user_id: str
    embed_provider: ProviderInterface


SYSTEM_PROMPT = """
You are Kizuna's memory agent. Given the user's latest message, decide what
durable facts (if any) should be remembered about the user.

Rules:
- You NEVER write memory yourself. You only PROPOSE operations; a separate
  policy layer decides what is actually committed, and can reject or
  transform any proposal.
- Use search_memory_tool and get_recent_memories_tool to check whether a
  candidate fact already exists, has changed, or conflicts with something
  already stored, before deciding NEW vs UPDATE vs SUPERSEDE vs
  EPISODIC_APPEND vs NOISE. You may call them more than once if there are
  several candidate facts in the message.
- memory_type 'profile' is for stable facts (job, relationships,
  preferences). 'episodic' is for time-bound events and feelings/states -- a
  changed feeling (e.g. loved the job Monday, hated it Friday) is a NEW
  episodic entry, never a SUPERSEDE of the earlier one.
- Label every proposal's source_kind: 'stated' if the user said it directly,
  'inferred' if you derived it from tone or context. Never label an
  inference as stated.
- If a candidate is sensitive (health, legal, financial, etc.), set
  is_sensitive=true; it will be held for the user's confirmation before it
  becomes active memory.
- If there is nothing durable to remember, return an empty list or a single
  NOISE entry. Do not invent facts that weren't stated or reasonably
  inferable from what was said.
"""

memory_agent = Agent(
    output_type=list[ProposedOperation],
    deps_type=MemoryAgentDeps,
    system_prompt=SYSTEM_PROMPT,
)


def _serialize(memory: dict) -> dict:
    return {
        "id": str(memory["id"]),
        "memory_type": memory["memory_type"],
        "content": memory["content"],
        "status": memory["status"],
        "confidence": memory.get("confidence"),
        "source_kind": memory.get("source_kind"),
        "user_verified": memory.get("user_verified"),
    }


@memory_agent.tool
async def search_memory_tool(ctx: RunContext[MemoryAgentDeps], query_text: str, top_k: int = 5) -> list[dict]:
    """Read-only: search the user's existing memories for ones related to query_text."""
    embedding = await embed_text(ctx.deps.embed_provider, query_text)
    results = await memory_store.search_memory(ctx.deps.pool, ctx.deps.user_id, embedding, top_k)
    return [_serialize(r) for r in results]


@memory_agent.tool
async def get_recent_memories_tool(ctx: RunContext[MemoryAgentDeps], limit: int = 5) -> list[dict]:
    """Read-only: the user's most recently created active memories, regardless of relevance."""
    memories = await memory_store.list_memories(ctx.deps.pool, ctx.deps.user_id)
    active = [m for m in memories if m["status"] == "active"]
    return [_serialize(m) for m in active[:limit]]


def default_model() -> Model:
    return OpenAIChatModel(os.environ.get("UTILITY_MODEL", "gpt-4o-mini"))


async def propose_memory_ops(
    pool: asyncpg.Pool,
    user_id: str,
    embed_provider: ProviderInterface,
    candidate_text: str,
    *,
    model: Model | None = None,
    tool_calls_limit: int = TOOL_CALL_BUDGET,
) -> list[ProposedOperation]:
    # tool_calls_limit is overridable (not just TOOL_CALL_BUDGET) because the
    # journal extraction path (worker.py) runs this per paragraph-sized
    # chunk of a long entry, not per short conversational turn, and needs a
    # tighter budget per call so a long entry can't fan out into dozens of
    # model round-trips.
    deps = MemoryAgentDeps(pool=pool, user_id=user_id, embed_provider=embed_provider)
    result = await memory_agent.run(
        candidate_text,
        deps=deps,
        model=model or default_model(),
        usage_limits=UsageLimits(tool_calls_limit=tool_calls_limit),
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
    )
    return result.output
