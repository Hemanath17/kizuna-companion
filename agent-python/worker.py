"""The durable cold-path worker (ARCHITECTURE.md 5.4, PLAN.md section 16
contract 8). Runnable as its own process, separate from the FastAPI app:

    python worker.py

Per outbox row it runs exactly one job:
  - tag_emotion: classify the turn's user message and store the label/score.
  - extract_memory: STUBBED. Only the cheap pre-filter runs for real; the
    memory agent that would actually extract facts lands in Phase 3 Step 3.3.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import asyncpg
from pydantic import BaseModel

from providers import OpenAIProvider, ProviderInterface
from token_trace import TokenTrace

logger = logging.getLogger("kizuna.worker")

MAX_ATTEMPTS = 5
LEASE_DURATION = timedelta(minutes=5)
POLL_INTERVAL_SECONDS = 1.0
RECONCILE_INTERVAL_SECONDS = 30.0

EMOTION_MAX_TOKENS = 50
EMOTION_NODE = "tag_emotion"


class EmotionClassification(BaseModel):
    label: Literal["joy", "sadness", "anger", "fear", "anxiety", "neutral"]
    score: float


@dataclass
class OutboxJob:
    id: str
    turn_id: str
    user_id: str
    job_type: str
    attempts: int


# Deliberately small and literal: this is the cheap, deterministic pre-filter
# the step requires, not a classifier. It only needs to catch the common
# no-fact case (acks, filler, pure feeling-only turns) so most turns skip the
# extraction call; anything it's unsure about is left to pass through.
_ACK_PHRASES = {
    "ok", "okay", "k", "kk", "lol", "lmao", "haha", "hehe", "yeah", "yep", "yup",
    "nope", "no", "yes", "sure", "thanks", "thank you", "cool", "nice", "fine",
    "alright", "right", "hmm", "hm", "oh", "wow", "ugh", "same", "true", "exactly",
}

_EMOTION_ONLY_PATTERN = re.compile(
    r"^(i'?m|i am|i feel|feeling)\s+(so\s+|really\s+|very\s+)?"
    r"(sad|happy|tired|anxious|angry|upset|excited|stressed|okay|ok|fine|good|great|bad|down|low)[.!]*$"
)


def is_fact_bearing(text: str) -> bool:
    """Cheap pre-filter for the extract_memory job: True if the turn might
    contain a durable fact worth the (expensive) real extraction call."""
    normalized = re.sub(r"[^\w\s']", "", text.strip().lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    if normalized in _ACK_PHRASES:
        return False
    if _EMOTION_ONLY_PATTERN.match(normalized):
        return False
    return len(normalized.split()) > 2


async def claim_job(pool: asyncpg.Pool, *, lease_duration: timedelta = LEASE_DURATION) -> OutboxJob | None:
    # SELECT ... FOR UPDATE SKIP LOCKED inside the same statement as the
    # UPDATE is what makes this claim atomic: a concurrent claim running at
    # the same instant skips this row (it's locked) rather than blocking,
    # so two workers never walk away with the same job. The OR branch is the
    # reclaim path for a lease a crashed worker never renewed.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            update outbox
            set status = 'processing', claimed_at = now(), lease_expires_at = now() + $1, attempts = attempts + 1
            where id = (
                select id from outbox
                where status = 'pending' or (status = 'processing' and lease_expires_at < now())
                order by created_at
                for update skip locked
                limit 1
            )
            returning id, turn_id, user_id, job_type, attempts
            """,
            lease_duration,
        )
    if row is None:
        return None
    return OutboxJob(
        id=str(row["id"]),
        turn_id=str(row["turn_id"]),
        user_id=str(row["user_id"]),
        job_type=row["job_type"],
        attempts=row["attempts"],
    )


async def _mark_done(pool: asyncpg.Pool, job_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute("update outbox set status = 'done' where id = $1", job_id)


async def _mark_failed(pool: asyncpg.Pool, job: OutboxJob) -> None:
    next_status = "dead_letter" if job.attempts >= MAX_ATTEMPTS else "pending"
    async with pool.acquire() as conn:
        await conn.execute("update outbox set status = $1 where id = $2", next_status, job.id)


async def reconcile_stuck_jobs(pool: asyncpg.Pool) -> int:
    """Proactively requeues (or dead-letters) jobs whose lease expired,
    instead of waiting for the next unrelated claim_job call to notice."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            update outbox
            set status = case when attempts >= $1 then 'dead_letter' else 'pending' end
            where status = 'processing' and lease_expires_at < now()
            returning id
            """,
            MAX_ATTEMPTS,
        )
    return len(rows)


async def handle_tag_emotion(pool: asyncpg.Pool, provider: ProviderInterface, job: OutboxJob) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select content from messages where turn_id = $1 and role = 'user'", job.turn_id
        )
    if row is None:
        return
    prompt = (
        "Classify the emotional tone of this message into exactly one label "
        "from: joy, sadness, anger, fear, anxiety, neutral. Give a 0-1 confidence score.\n\n"
        f"Message: {row['content']}"
    )
    tracer = TokenTrace(turn_id=job.turn_id)
    classification = await provider.complete_structured(
        prompt, EmotionClassification, EMOTION_MAX_TOKENS, node=EMOTION_NODE, tracer=tracer
    )
    tracer.log()
    async with pool.acquire() as conn:
        await conn.execute(
            "update messages set emotion_label = $1, emotion_score = $2 where turn_id = $3 and role = 'user'",
            classification.label,
            classification.score,
            job.turn_id,
        )


async def handle_extract_memory(pool: asyncpg.Pool, provider: ProviderInterface, job: OutboxJob) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select content, created_at from messages where turn_id = $1 and role = 'user'", job.turn_id
        )
    if row is None or not is_fact_bearing(row["content"]):
        logger.info("extract_memory_skipped turn_id=%s reason=pre_filter", job.turn_id)
        return

    # Imported lazily: memory_agent/policy pull in pydantic_ai, which only
    # this job type needs, and it keeps worker.py importable (e.g. for the
    # pre-filter alone) without that dependency resolving at import time.
    from memory_agent import propose_memory_ops
    from policy import commit_proposed_operations

    proposals = await propose_memory_ops(pool, job.user_id, provider, row["content"])
    decisions = await commit_proposed_operations(
        pool, job.user_id, provider, proposals, turn_timestamp=row["created_at"]
    )
    logger.info(
        "extract_memory_committed turn_id=%s proposals=%d accepted=%d",
        job.turn_id,
        len(proposals),
        sum(1 for d in decisions if d.accepted),
    )


JOB_HANDLERS = {
    "tag_emotion": handle_tag_emotion,
    "extract_memory": handle_extract_memory,
}


async def process_one(pool: asyncpg.Pool, provider: ProviderInterface) -> bool:
    """Claims and runs exactly one job. Returns False if none was pending."""
    job = await claim_job(pool)
    if job is None:
        return False
    handler = JOB_HANDLERS.get(job.job_type)
    try:
        if handler is None:
            raise ValueError(f"unknown job_type: {job.job_type}")
        await handler(pool, provider, job)
    except Exception:
        logger.exception("job_failed job_id=%s job_type=%s attempts=%d", job.id, job.job_type, job.attempts)
        await _mark_failed(pool, job)
        return True
    await _mark_done(pool, job.id)
    return True


async def run_worker(
    pool: asyncpg.Pool,
    provider: ProviderInterface,
    *,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    reconcile_interval: float = RECONCILE_INTERVAL_SECONDS,
) -> None:
    loop = asyncio.get_event_loop()
    last_reconcile = loop.time()
    while True:
        worked = await process_one(pool, provider)
        now = loop.time()
        if now - last_reconcile >= reconcile_interval:
            await reconcile_stuck_jobs(pool)
            last_reconcile = now
        if not worked:
            await asyncio.sleep(poll_interval)


async def _main() -> None:
    from db import create_worker_pool

    pool = await create_worker_pool()
    provider: ProviderInterface = OpenAIProvider()
    await run_worker(pool, provider)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main())
