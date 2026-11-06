import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import memory_agent
import memory_store
import reflection_agent
from fake_openai import FakeAsyncOpenAIClient
from providers import EchoProvider, OpenAIProvider
from worker import (
    JOURNAL_CHUNK_TOOL_CALLS_LIMIT,
    JOURNAL_PROPOSAL_CAP,
    MAX_ATTEMPTS,
    EmotionClassification,
    _chunk_journal_body,
    claim_job,
    is_fact_bearing,
    process_one,
    reconcile_stuck_jobs,
)


def _emotion_provider(label: str = "sadness", score: float = 0.8) -> OpenAIProvider:
    fake_client = FakeAsyncOpenAIClient(
        parse_fn=lambda kwargs: (EmotionClassification(label=label, score=score), None),
    )
    return OpenAIProvider(client=fake_client)


async def _seed_turn(test_database, text: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    turn_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    await test_database.seed_user_message(user_id, conversation_id, turn_id, text)
    return user_id, conversation_id, turn_id


@pytest.mark.asyncio
async def test_two_concurrent_workers_never_claim_the_same_row(worker_pool, test_database):
    user_1, _, turn_1 = await _seed_turn(test_database, "I have a big interview Friday at Acme")
    user_2, _, turn_2 = await _seed_turn(test_database, "I'm starting a new job next week at Globex")
    await test_database.seed_outbox_job(turn_1, user_1, "tag_emotion")
    await test_database.seed_outbox_job(turn_2, user_2, "tag_emotion")

    job_a, job_b = await asyncio.gather(claim_job(worker_pool), claim_job(worker_pool))

    assert job_a is not None and job_b is not None
    assert job_a.id != job_b.id
    assert {job_a.turn_id, job_b.turn_id} == {str(turn_1), str(turn_2)}


@pytest.mark.asyncio
async def test_tag_emotion_job_classifies_and_stores_label_and_score(worker_pool, test_database):
    user_id, _, turn_id = await _seed_turn(test_database, "I'm really anxious about tomorrow")
    job_id = await test_database.seed_outbox_job(turn_id, user_id, "tag_emotion")
    provider = _emotion_provider(label="anxiety", score=0.75)

    worked = await process_one(worker_pool, provider)

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    messages = await test_database.superuser_fetch(
        "select emotion_label, emotion_score from messages where turn_id = $1 and role = 'user'", turn_id
    )
    assert messages[0]["emotion_label"] == "anxiety"
    assert messages[0]["emotion_score"] == pytest.approx(0.75)


@pytest.mark.asyncio
async def test_crash_mid_job_then_restart_completes_exactly_once_no_duplicates(worker_pool, test_database):
    user_id, _, turn_id = await _seed_turn(test_database, "I'm really anxious about tomorrow")
    job_id = await test_database.seed_outbox_job(turn_id, user_id, "tag_emotion")

    # Simulate a worker claiming the job then dying before it finishes:
    # claim it, but never run the handler or mark it done.
    crashed_claim = await claim_job(worker_pool)
    assert crashed_claim.id == str(job_id)
    row_after_crash = await test_database.fetch_outbox_row(job_id)
    assert row_after_crash["status"] == "processing"

    # Simulate time passing past the lease without a real 5-minute wait.
    await test_database.expire_lease(job_id)

    provider = _emotion_provider(label="anxiety", score=0.6)
    worked = await process_one(worker_pool, provider)

    assert worked is True
    final_row = await test_database.fetch_outbox_row(job_id)
    assert final_row["status"] == "done"
    assert final_row["attempts"] == 2  # claimed twice: the crashed attempt + the reclaim
    messages = await test_database.superuser_fetch(
        "select emotion_label from messages where turn_id = $1 and role = 'user'", turn_id
    )
    assert len(messages) == 1  # tag_emotion UPDATEs in place; reprocessing can't duplicate the row
    assert messages[0]["emotion_label"] == "anxiety"


@pytest.mark.asyncio
async def test_lease_expiry_reclaims_a_stuck_job_via_reconciliation(worker_pool, test_database):
    user_id, _, turn_id = await _seed_turn(test_database, "I'm really anxious about tomorrow")
    job_id = await test_database.seed_outbox_job(turn_id, user_id, "tag_emotion")

    await claim_job(worker_pool)
    await test_database.expire_lease(job_id)

    swept = await reconcile_stuck_jobs(worker_pool)

    assert swept == 1
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_repeatedly_failing_job_stops_after_retry_cap_and_dead_letters(worker_pool, test_database):
    user_id, _, turn_id = await _seed_turn(test_database, "I'm really anxious about tomorrow")
    job_id = await test_database.seed_outbox_job(turn_id, user_id, "unknown_job_type")

    for _ in range(MAX_ATTEMPTS):
        worked = await process_one(worker_pool, _emotion_provider())
        assert worked is True
        row = await test_database.fetch_outbox_row(job_id)
        if row["status"] == "dead_letter":
            break
        assert row["status"] == "pending"
        # Make it immediately reclaimable instead of waiting out the lease.
        await test_database.expire_lease(job_id)

    final_row = await test_database.fetch_outbox_row(job_id)
    assert final_row["status"] == "dead_letter"
    assert final_row["attempts"] >= MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_extract_memory_job_runs_agent_and_policy_end_to_end(worker_pool, pool, test_database, monkeypatch):
    # Proves the worker's own BYPASSRLS identity can still reach memory_store
    # (which demands SET LOCAL ROLE authenticated -- migration 0006's grant)
    # and that a fact-bearing turn ends up committed as a real memory row.
    user_id, _, turn_id = await _seed_turn(test_database, "I work as a data engineer at Acme")
    job_id = await test_database.seed_outbox_job(turn_id, user_id, "extract_memory")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=out_tool.name,
                    args={
                        "response": [
                            {
                                "op": "NEW",
                                "content": "works as a data engineer at Acme",
                                "memory_type": "profile",
                                "confidence": 0.9,
                                "is_sensitive": False,
                                "source_kind": "stated",
                                "reason": "user said so directly",
                            }
                        ]
                    },
                )
            ]
        )

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    memories = await memory_store.list_memories(pool, str(user_id))
    assert len(memories) == 1
    assert memories[0]["content"] == "works as a data engineer at Acme"
    assert memories[0]["status"] == "active"


def test_chunk_journal_body_splits_on_paragraph_boundaries_within_token_budget():
    short = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert _chunk_journal_body(short) == [short]  # well under the target, one chunk

    oversized_paragraph = "word " * 500  # ~2500 chars, over the ~1800-char target on its own
    chunks = _chunk_journal_body(f"{oversized_paragraph}\n\nshort second paragraph")
    assert len(chunks) == 2  # the oversized paragraph is kept whole, not split mid-paragraph
    assert chunks[0].strip() == oversized_paragraph.strip()

    assert _chunk_journal_body("   \n\n  ") == []  # whitespace-only entry has no chunks


@pytest.mark.asyncio
async def test_journal_memory_extraction_job_chunks_extracts_and_tags_provenance(worker_pool, pool, test_database, monkeypatch):
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
    body = (
        "I got a promotion to senior engineer at Acme this week.\n\n"
        "We also adopted a rescue dog named Biscuit over the weekend."
    )
    entry_id = await test_database.seed_journal_entry(user_id, body)
    job_id = await test_database.seed_outbox_job(entry_id, user_id, "journal_memory_extraction")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        prompt = ""
        for message in reversed(messages):
            if isinstance(message, ModelRequest):
                for part in reversed(message.parts):
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        prompt = part.content
                        break
                break
        # The two short paragraphs merge into one chunk under the token
        # budget (see test_chunk_journal_body_... above), so one real model
        # call sees both facts and must propose both -- exactly the
        # list[ProposedOperation] shape the real agent produces.
        ops = []
        if "promotion" in prompt:
            ops.append(
                {
                    "op": "NEW", "content": "promoted to senior engineer at Acme", "memory_type": "profile",
                    "confidence": 0.9, "is_sensitive": False,
                    "source_kind": "inferred",  # deliberately wrong, to prove the worker forces 'stated'
                    "reason": "oracle",
                }
            )
        if "Biscuit" in prompt:
            ops.append(
                {
                    "op": "NEW", "content": "adopted a dog named Biscuit", "memory_type": "episodic",
                    "confidence": 0.9, "is_sensitive": False, "source_kind": "inferred", "reason": "oracle",
                }
            )
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": ops})])

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    memories = await memory_store.list_memories(pool, str(user_id))
    assert len(memories) == 2
    contents = {m["content"] for m in memories}
    assert contents == {"promoted to senior engineer at Acme", "adopted a dog named Biscuit"}
    for memory in memories:
        assert memory["source_kind"] == "stated"  # forced, regardless of what the oracle proposed
        assert str(memory["source_journal_entry_id"]) == str(entry_id)
        assert memory["source_message_id"] is None


@pytest.mark.asyncio
async def test_journal_extraction_respects_the_per_entry_proposal_cap(worker_pool, pool, test_database, monkeypatch):
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())

    # 12 oversized paragraphs, each kept whole as its own chunk (see
    # _chunk_journal_body_splits... above) -- more chunks than the cap
    # allows, so it's the cap, not the chunk count, that limits commits.
    paragraphs = [
        ("Padding padding padding padding padding padding padding padding padding padding. " * 20)
        + f" This paragraph is entry number {i}."
        for i in range(12)
    ]
    entry_id = await test_database.seed_journal_entry(user_id, "\n\n".join(paragraphs))
    job_id = await test_database.seed_outbox_job(entry_id, user_id, "journal_memory_extraction")

    counter = iter(range(1, 100))

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        op = {
            "op": "NEW", "content": f"fact number {next(counter)} happened today", "memory_type": "episodic",
            "confidence": 0.9, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": [op]})])

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    memories = await memory_store.list_memories(pool, str(user_id))
    assert 0 < len(memories) <= JOURNAL_PROPOSAL_CAP


@pytest.mark.asyncio
async def test_a_2000_word_journal_entry_completes_extraction_without_exceeding_budgets(
    worker_pool, pool, test_database, monkeypatch
):
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())

    # ~2000 words across 20 distinct paragraphs (PLAN.md Step 4.2 acceptance).
    paragraphs = [
        " ".join([f"reflecting-on-day-{i}"] * 100)  # 100 words/paragraph * 20 = ~2000 words
        for i in range(20)
    ]
    entry_id = await test_database.seed_journal_entry(user_id, "\n\n".join(paragraphs))
    job_id = await test_database.seed_outbox_job(entry_id, user_id, "journal_memory_extraction")

    counter = iter(range(1, 100))

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        op = {
            "op": "NEW", "content": f"fact number {next(counter)} happened today", "memory_type": "episodic",
            "confidence": 0.9, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": [op]})])

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True  # completes without raising (no UsageLimitExceeded, no unhandled error)
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    memories = await memory_store.list_memories(pool, str(user_id))
    assert len(memories) <= JOURNAL_PROPOSAL_CAP


@pytest.mark.asyncio
async def test_journal_memory_extraction_completes_when_a_chunk_needs_more_than_the_old_tight_limit(
    worker_pool, pool, test_database, monkeypatch
):
    """Step 4.5/4.6/4.7: against the real model, a chunk with several distinct
    facts routinely needs more than 3 tool calls (searches/get-recent) before
    it can finalize -- the old JOURNAL_CHUNK_TOOL_CALLS_LIMIT=3 made this
    dead-letter deterministically every time. This oracle scripts exactly
    that shape (4 intermediate tool calls, then the final answer) -- more
    than the old limit, within the new one -- and proves the job now
    completes end to end instead of raising UsageLimitExceeded."""
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
    body = (
        "Work has been a lot lately, but it paid off: I got a promotion to senior "
        "engineer at Acme this week, which feels great after all those late nights.\n\n"
        "On a completely different note, we adopted a rescue dog named Biscuit over "
        "the weekend. She's nervous around loud noises but already loves the backyard.\n\n"
        "I've also started training for a half marathon in the spring. My knees are a "
        "little sore today, but I'm sticking with the plan."
    )
    entry_id = await test_database.seed_journal_entry(user_id, body)
    job_id = await test_database.seed_outbox_job(entry_id, user_id, "journal_memory_extraction")

    assert JOURNAL_CHUNK_TOOL_CALLS_LIMIT > 3, "test assumes the limit was raised above the old value of 3"
    calls = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] <= 4:  # 4 intermediate calls: exceeds the old limit (3), within the new one
            return ModelResponse(parts=[ToolCallPart(tool_name="search_memory_tool", args={"query_text": "x"})])
        out_tool = info.output_tools[0]
        ops = [
            {
                "op": "NEW", "content": "promoted to senior engineer at Acme", "memory_type": "profile",
                "confidence": 0.9, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
            },
            {
                "op": "NEW", "content": "adopted a dog named Biscuit", "memory_type": "episodic",
                "confidence": 0.9, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
            },
            {
                "op": "NEW", "content": "training for a half marathon", "memory_type": "episodic",
                "confidence": 0.8, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
            },
        ]
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": ops})])

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"  # not dead_letter
    memories = await memory_store.list_memories(pool, str(user_id))
    assert len(memories) == 3


@pytest.mark.asyncio
async def test_journal_memory_extraction_degrades_gracefully_when_a_chunk_never_converges(
    worker_pool, pool, test_database, monkeypatch
):
    """The real model's tool-calling isn't reliably convergent (Step 4.7
    measurement): no fixed limit guarantees a chunk finalizes. This oracle
    makes one chunk converge normally and scripts a second, deliberately
    pathological chunk that always calls a tool and never produces the final
    answer -- proving the job still completes (partial result), rather than
    losing the whole entry's work and dead-lettering."""
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())

    # First paragraph alone is padded past the chunk token budget (same
    # technique as the proposal-cap test above), so it's kept whole as its
    # own chunk and the second paragraph is forced into a separate one --
    # guarantees two distinct chunks, not one merged chunk.
    convergent_paragraph = (
        "Padding padding padding padding padding padding padding padding padding padding. " * 25
    ) + " I got a promotion to senior engineer at Acme this week."
    pathological_paragraph = "PATHOLOGICAL_CHUNK_MARKER lots of distinct facts happened today, six of them at least."
    entry_id = await test_database.seed_journal_entry(
        user_id, convergent_paragraph + "\n\n" + pathological_paragraph
    )
    job_id = await test_database.seed_outbox_job(entry_id, user_id, "journal_memory_extraction")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Each chunk gets its own fresh agent run, so there is exactly one
        # real UserPromptPart anywhere in this run's history -- the chunk
        # text itself, always in the FIRST ModelRequest. Only looking at the
        # latest ModelRequest breaks on round 2+ of a chunk's own
        # conversation, once that latest request is a tool-return follow-up
        # instead of the original prompt.
        prompt = ""
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        prompt = part.content
                        break
                if prompt:
                    break
        if "PATHOLOGICAL_CHUNK_MARKER" in prompt:
            return ModelResponse(parts=[ToolCallPart(tool_name="search_memory_tool", args={"query_text": "x"})])
        out_tool = info.output_tools[0]
        op = {
            "op": "NEW", "content": "promoted to senior engineer at Acme", "memory_type": "profile",
            "confidence": 0.9, "is_sensitive": False, "source_kind": "stated", "reason": "oracle",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": [op]})])

    monkeypatch.setattr(memory_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"  # partial result, not dead_letter
    memories = await memory_store.list_memories(pool, str(user_id))
    assert len(memories) == 1
    assert memories[0]["content"] == "promoted to senior engineer at Acme"


@pytest.mark.asyncio
async def test_reflection_generate_job_validates_and_marks_ready_on_first_try(worker_pool, pool, test_database, monkeypatch):
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=7)
    reflection_id = await test_database.seed_reflection(user_id, period_start, period_end)
    job_id = await test_database.seed_outbox_job(reflection_id, user_id, "reflection_generate")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        body = (
            "This week I noticed things felt fairly steady for you. Hope the quieter moments were a "
            "good kind of quiet."
        )
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"body": body, "memory_ids_used": []})])

    monkeypatch.setattr(reflection_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    reflection = await test_database.fetch_reflection(reflection_id)
    assert reflection["status"] == "ready"
    assert reflection["body"] is not None
    assert reflection["generated_at"] is not None
    assert reflection["validator_version"] is not None


@pytest.mark.asyncio
async def test_reflection_generate_retries_once_then_rejects_and_never_stores_unvalidated_prose(
    worker_pool, pool, test_database, monkeypatch
):
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=7)
    reflection_id = await test_database.seed_reflection(user_id, period_start, period_end)
    job_id = await test_database.seed_outbox_job(reflection_id, user_id, "reflection_generate")

    attempts = {"n": 0}

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        attempts["n"] += 1
        out_tool = info.output_tools[0]
        # Always produces dependency language, on both the first attempt
        # and the retry -- proving the worker gives up after exactly one
        # retry rather than looping, and never stores this body.
        body = "I missed you this week and I was waiting to hear from you again."
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"body": body, "memory_ids_used": []})])

    monkeypatch.setattr(reflection_agent, "default_model", lambda: FunctionModel(fn))

    worked = await process_one(worker_pool, EchoProvider())

    assert worked is True
    assert attempts["n"] == 2  # one try + exactly one retry
    row = await test_database.fetch_outbox_row(job_id)
    assert row["status"] == "done"
    reflection = await test_database.fetch_reflection(reflection_id)
    assert reflection["status"] == "rejected"
    assert reflection["body"] is None  # never stored, even transiently
    assert reflection["rejection_reason"] is not None
    assert reflection["rejection_reason"].startswith("dependency_language")


@pytest.mark.asyncio
async def test_rejected_reflection_is_never_visible_via_list_memories_style_query(worker_pool, pool, test_database, monkeypatch):
    # Not a memory query -- a direct proof that the rejected row's body
    # column stays null at the DB level, which is what every .NET read
    # endpoint's status filter relies on to keep it from ever surfacing.
    user_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, uuid.uuid4())
    period_end = datetime.now(timezone.utc)
    period_start = period_end - timedelta(days=7)
    reflection_id = await test_database.seed_reflection(user_id, period_start, period_end)
    await test_database.seed_outbox_job(reflection_id, user_id, "reflection_generate")

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        body = "You are diagnosed with an anxiety disorder based on what you've shared this week."
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"body": body, "memory_ids_used": []})])

    monkeypatch.setattr(reflection_agent, "default_model", lambda: FunctionModel(fn))
    await process_one(worker_pool, EchoProvider())

    reflection = await test_database.fetch_reflection(reflection_id)
    assert reflection["status"] == "rejected"
    assert reflection["body"] is None
    assert reflection["rejection_reason"].startswith("clinical_language")


def test_pre_filter_skips_no_fact_turns_and_passes_fact_bearing_ones():
    no_fact_turns = ["ok", "lol", "okay", "I'm so sad", "I feel anxious", "lol ok", "k", ""]
    fact_bearing_turns = [
        "I have a big interview Friday at Acme",
        "I work as a data engineer at Google",
        "I feel sad because I lost my job today",
    ]

    for text in no_fact_turns:
        assert is_fact_bearing(text) is False, text
    for text in fact_bearing_turns:
        assert is_fact_bearing(text) is True, text
