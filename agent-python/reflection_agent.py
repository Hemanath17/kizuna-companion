"""The reflection agent: PROPOSES a reflection's prose, never shows it
(ARCHITECTURE.md 5.11, PLAN.md section 2b, section 9, Phase 4 decisions).
The same constrained-autonomy shape as memory_agent.py, applied to generated
prose instead of structured memory operations: reflection_validator.py is
the deterministic layer that decides whether anything this module produces
may ever reach the user.
"""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from dataclasses import dataclass  # noqa: E402
from datetime import datetime  # noqa: E402

import asyncpg  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from pydantic_ai import Agent, RunContext, UsageLimits  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

from db import user_scoped_transaction  # noqa: E402

REFLECTION_PROMPT_VERSION = "v1"

# Generous enough for a couple of read calls over the window; bounded so one
# reflection can never trigger unbounded model round-trips.
TOOL_CALL_BUDGET = 6
MAX_OUTPUT_TOKENS = 500


class ReflectionOutput(BaseModel):
    body: str
    memory_ids_used: list[str]


@dataclass
class ReflectionAgentDeps:
    pool: asyncpg.Pool
    user_id: str
    period_start: datetime
    period_end: datetime


SYSTEM_PROMPT = """
You are Kizuna's reflection agent. Write one short, warm reflection on the
user's recent period, grounded ONLY in what list_memories_in_window_tool and
list_messages_in_window_tool actually return.

Rules:
- Never invent a specific fact, name, or date that isn't in what the tools
  returned. If there is too little material, say so briefly and honestly
  rather than filling in detail.
- Never use language that manufactures pressure to return, implies the app
  misses the user, or implies a human-like exclusive bond -- no "I missed
  you", "I was waiting for you", "come back soon", "I need you", or
  anything in that spirit. Ordinary first-person observation ("I noticed...",
  "what struck me...") is fine and expected; dependency language is not.
- Never give a diagnosis, never claim the user has a clinical condition,
  never give medication or prescriptive medical advice.
- End grounded: leave the user a little lighter, or with one small concrete
  observation -- never a hook to come back.
- List every memory id you actually drew on in memory_ids_used. An empty
  list is fine if you only used messages or nothing specific.
"""

reflection_agent = Agent(
    output_type=ReflectionOutput,
    deps_type=ReflectionAgentDeps,
    system_prompt=SYSTEM_PROMPT,
)


@reflection_agent.tool
async def list_memories_in_window_tool(ctx: RunContext[ReflectionAgentDeps]) -> list[dict]:
    """Read-only: the user's active memories created within the window."""
    import memory_store

    memories = await memory_store.list_memories(ctx.deps.pool, ctx.deps.user_id, status="active")
    windowed = [m for m in memories if ctx.deps.period_start <= m["created_at"] < ctx.deps.period_end]
    return [{"id": str(m["id"]), "content": m["content"], "memory_type": m["memory_type"]} for m in windowed]


@reflection_agent.tool
async def list_messages_in_window_tool(ctx: RunContext[ReflectionAgentDeps], limit: int = 20) -> list[dict]:
    """Read-only: the user's own chat messages within the window."""
    async with ctx.deps.pool.acquire() as conn, user_scoped_transaction(conn, ctx.deps.user_id):
        rows = await conn.fetch(
            """
            select content, created_at from messages
            where role = 'user' and created_at >= $1 and created_at < $2
            order by created_at
            limit $3
            """,
            ctx.deps.period_start,
            ctx.deps.period_end,
            limit,
        )
    return [{"content": r["content"], "created_at": r["created_at"].isoformat()} for r in rows]


def default_model() -> Model:
    return OpenAIChatModel(os.environ.get("UTILITY_MODEL", "gpt-4o-mini"))


async def propose_reflection(
    pool: asyncpg.Pool,
    user_id: str,
    period_start: datetime,
    period_end: datetime,
    *,
    model: Model | None = None,
    extra_instruction: str | None = None,
) -> ReflectionOutput:
    """extra_instruction is how the worker's one-retry-with-a-stricter-
    instruction step (ARCHITECTURE.md 5.11) is implemented: appended to the
    user prompt rather than mutating the agent's fixed system_prompt."""
    deps = ReflectionAgentDeps(pool=pool, user_id=user_id, period_start=period_start, period_end=period_end)
    prompt = f"Write a reflection for the period {period_start.isoformat()} to {period_end.isoformat()}."
    if extra_instruction:
        prompt = f"{prompt}\n\n{extra_instruction}"
    result = await reflection_agent.run(
        prompt,
        deps=deps,
        model=model or default_model(),
        usage_limits=UsageLimits(tool_calls_limit=TOOL_CALL_BUDGET),
        model_settings=ModelSettings(max_tokens=MAX_OUTPUT_TOKENS),
    )
    return result.output
