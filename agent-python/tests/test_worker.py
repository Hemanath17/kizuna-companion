import asyncio
import uuid

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import memory_agent
import memory_store
from fake_openai import FakeAsyncOpenAIClient
from providers import EchoProvider, OpenAIProvider
from worker import (
    MAX_ATTEMPTS,
    EmotionClassification,
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
