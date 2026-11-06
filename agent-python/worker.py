"""The durable cold-path worker (ARCHITECTURE.md 5.4, PLAN.md section 16
contract 8). Runnable as its own process, separate from the FastAPI app:

    python worker.py

Per outbox row it runs exactly one job:
  - tag_emotion: classify the turn's user message and store the label/score.
  - extract_memory: memory_agent + policy.py over one chat turn.
  - journal_memory_extraction: the same pipeline, chunked, over a journal
    entry (Phase 4 Step 4.2).
  - reflection_generate: reflection_agent + reflection_validator.py -- a
    generated reflection that fails validation twice is rejected and never
    shown (Phase 4 Step 4.3).
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

import asyncpg
from pydantic import BaseModel
from pydantic_ai.exceptions import UsageLimitExceeded

from providers import OpenAIProvider, ProviderInterface
from token_trace import TokenTrace

logger = logging.getLogger("kizuna.worker")

MAX_ATTEMPTS = 5
LEASE_DURATION = timedelta(minutes=5)
POLL_INTERVAL_SECONDS = 1.0
RECONCILE_INTERVAL_SECONDS = 30.0

EMOTION_MAX_TOKENS = 50
EMOTION_NODE = "tag_emotion"

# Journal entries are long-form, unlike short conversational turns, so
# extraction (Phase 4 Step 4.2) chunks on paragraph boundaries and runs the
# memory agent per chunk with a reduced tool-call budget, bounded by a
# per-entry cap on total committed proposals.
_CHARS_PER_TOKEN = 4  # same no-tokenizer heuristic as context_builder.py
JOURNAL_CHUNK_TARGET_TOKENS = 450
# Was 3, measured against a scripted oracle in unit tests only. Against the
# real model (Step 4.6), a chunk with 2-3 distinct facts that converges
# normally needs ~5 tool calls (searches/get-recent + the final answer), so 3
# was too tight even for the common case, not just an edge case. But the real
# model is also not reliably convergent: the same 2-fact input needed 47 tool
# calls on one run and 5 on another, and a 4-fact chunk spiralled past 80
# without ever producing a final answer. No fixed number eliminates that
# risk, so 10 (2x headroom over the typical ~5-call convergence) is set to
# absorb normal variance cheaply, while graceful degradation below -- not a
# bigger number -- is what actually protects against the non-convergent
# cases.
JOURNAL_CHUNK_TOOL_CALLS_LIMIT = 10
JOURNAL_PROPOSAL_CAP = 10


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


def _chunk_journal_body(body: str, *, target_tokens: int = JOURNAL_CHUNK_TARGET_TOKENS) -> list[str]:
    """Splits on paragraph (blank-line) boundaries into windows of roughly
    target_tokens, using the same chars-per-token heuristic as
    context_builder.py rather than a tokenizer dependency. A single
    paragraph longer than the target is kept whole, not split mid-paragraph
    -- the boundary rule the step asked for takes priority over a hard size
    cap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        return []
    target_chars = target_tokens * _CHARS_PER_TOKEN
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in paragraphs:
        if current and current_len + len(paragraph) > target_chars:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


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
            "select id, content, created_at from messages where turn_id = $1 and role = 'user'", job.turn_id
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
        pool, job.user_id, provider, proposals, turn_timestamp=row["created_at"], source_message_id=str(row["id"])
    )
    logger.info(
        "extract_memory_committed turn_id=%s proposals=%d accepted=%d",
        job.turn_id,
        len(proposals),
        sum(1 for d in decisions if d.accepted),
    )


async def handle_journal_memory_extraction(pool: asyncpg.Pool, provider: ProviderInterface, job: OutboxJob) -> None:
    # outbox.turn_id is reused as a generic "subject id" here -- it holds
    # the journal entry's id (see JournalRepository.CreateAsync in .NET),
    # not a chat turn_id. The column carries no FK, so this is a safe reuse.
    async with pool.acquire() as conn:
        row = await conn.fetchrow("select body, created_at from journal_entries where id = $1", job.turn_id)
    if row is None:
        logger.info("journal_memory_extraction_skipped entry_id=%s reason=not_found", job.turn_id)
        return

    from memory_agent import propose_memory_ops
    from policy import commit_proposed_operations

    chunks = [chunk for chunk in _chunk_journal_body(row["body"]) if is_fact_bearing(chunk)]
    total_committed = 0
    truncated_chunks = 0
    for chunk_index, chunk in enumerate(chunks):
        if total_committed >= JOURNAL_PROPOSAL_CAP:
            logger.info(
                "journal_memory_extraction_cap_hit entry_id=%s cap=%d committed=%d",
                job.turn_id, JOURNAL_PROPOSAL_CAP, total_committed,
            )
            break

        # The real model's tool-calling behavior per chunk is not reliably
        # convergent (Step 4.6/4.7 measurement): a chunk can exceed
        # JOURNAL_CHUNK_TOOL_CALLS_LIMIT no matter how high it's set. Under
        # pydantic_ai's single-final-answer design, no partial proposals
        # exist yet when that happens (the structured output is only
        # produced on a successful final call), so there is nothing to
        # commit for THIS chunk -- but letting the exception propagate used
        # to abort the whole job, silently losing every other chunk's facts
        # too and burning all 5 retry attempts into dead_letter on a chunk
        # that will fail the same way every time. Catching it here instead
        # logs the loss explicitly and keeps processing the entry's
        # remaining chunks, so a partial result replaces a silent total one.
        try:
            proposals = await propose_memory_ops(
                pool, job.user_id, provider, chunk, tool_calls_limit=JOURNAL_CHUNK_TOOL_CALLS_LIMIT
            )
        except UsageLimitExceeded as exc:
            truncated_chunks += 1
            logger.warning(
                "journal_memory_extraction_chunk_truncated entry_id=%s chunk_index=%d "
                "chunk_chars=%d limit=%d error=%s",
                job.turn_id, chunk_index, len(chunk), JOURNAL_CHUNK_TOOL_CALLS_LIMIT, exc,
            )
            continue

        # The user wrote every word of a journal entry themselves: source_kind
        # is forced to 'stated' regardless of what the agent proposed
        # (PLAN.md Phase 4 decision 7) -- the same deterministic-override
        # pattern policy.py already uses for pending_confirmation.
        proposals = [p.model_copy(update={"source_kind": "stated"}) for p in proposals]

        decisions = await commit_proposed_operations(
            pool, job.user_id, provider, proposals,
            turn_timestamp=row["created_at"], source_journal_entry_id=job.turn_id,
        )
        total_committed += sum(1 for d in decisions if d.accepted)

    logger.info(
        "journal_memory_extraction_committed entry_id=%s chunks=%d committed=%d truncated_chunks=%d",
        job.turn_id, len(chunks), total_committed, truncated_chunks,
    )


async def handle_reflection_generate(pool: asyncpg.Pool, provider: ProviderInterface, job: OutboxJob) -> None:
    # outbox.turn_id is reused as a generic "subject id" here too -- it
    # holds the reflection's id (see ReflectionRepository.CreateOrGetAsync
    # in .NET).
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select period_start, period_end from reflections where id = $1 and user_id = $2",
            job.turn_id, job.user_id,
        )
    if row is None:
        logger.info("reflection_generate_skipped reflection_id=%s reason=not_found", job.turn_id)
        return

    import memory_store
    from reflection_agent import REFLECTION_PROMPT_VERSION, propose_reflection
    from reflection_validator import REFLECTION_VALIDATOR_VERSION, check_grounding_soft, validate_reflection

    period_start, period_end = row["period_start"], row["period_end"]

    memories_in_window = [
        m for m in await memory_store.list_memories(pool, job.user_id, status="active")
        if period_start <= m["created_at"] < period_end
    ]
    known_memory_ids = {str(m["id"]) for m in memories_in_window}
    source_texts = [m["content"] for m in memories_in_window]
    async with pool.acquire() as conn:
        message_rows = await conn.fetch(
            """
            select content from messages
            where user_id = $1 and role = 'user' and created_at >= $2 and created_at < $3
            """,
            job.user_id, period_start, period_end,
        )
    source_texts += [r["content"] for r in message_rows]

    model = os.environ.get("UTILITY_MODEL", "gpt-4o-mini")
    rejection_reason: str | None = None
    for attempt_num in range(2):  # the one retry ARCHITECTURE.md 5.11/PLAN.md Step 4.3 asks for
        extra_instruction = None
        if attempt_num == 1:
            extra_instruction = (
                f"Your previous attempt was rejected ({rejection_reason}). Revise: stay strictly "
                "grounded in what the tools returned, avoid any dependency, re-engagement, or "
                "clinical/diagnostic language, and keep it concise."
            )
        output = await propose_reflection(
            pool, job.user_id, period_start, period_end, extra_instruction=extra_instruction
        )
        result = validate_reflection(output.body, output.memory_ids_used, known_memory_ids)
        if result.ok:
            suspicious = check_grounding_soft(output.body, source_texts)
            if suspicious:
                logger.warning(
                    "reflection_grounding_soft_check reflection_id=%s suspicious_terms=%s",
                    job.turn_id, suspicious,
                )
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    update reflections
                    set body = $1, status = 'ready', prompt_version = $2, validator_version = $3,
                        model = $4, generated_at = now()
                    where id = $5
                    """,
                    output.body, REFLECTION_PROMPT_VERSION, REFLECTION_VALIDATOR_VERSION, model, job.turn_id,
                )
            logger.info("reflection_generate_ready reflection_id=%s attempt=%d", job.turn_id, attempt_num + 1)
            return
        rejection_reason = result.reason
        logger.info(
            "reflection_generate_validation_failed reflection_id=%s attempt=%d reason=%s",
            job.turn_id, attempt_num + 1, rejection_reason,
        )

    # Both attempts failed validation: never fall back to displaying
    # unvalidated prose (PLAN.md Phase 4 decision 8) -- body stays null.
    async with pool.acquire() as conn:
        await conn.execute(
            """
            update reflections
            set status = 'rejected', rejection_reason = $1, prompt_version = $2, validator_version = $3
            where id = $4
            """,
            rejection_reason, REFLECTION_PROMPT_VERSION, REFLECTION_VALIDATOR_VERSION, job.turn_id,
        )
    logger.info("reflection_generate_rejected reflection_id=%s reason=%s", job.turn_id, rejection_reason)


JOB_HANDLERS = {
    "tag_emotion": handle_tag_emotion,
    "extract_memory": handle_extract_memory,
    "journal_memory_extraction": handle_journal_memory_extraction,
    "reflection_generate": handle_reflection_generate,
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
