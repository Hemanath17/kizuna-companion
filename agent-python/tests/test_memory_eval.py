"""Proves the memory eval harness mechanics (load -> seed -> run -> score)
work end to end against the real DB-backed pipeline, the same way
test_eval.py's oracle proves the safety harness -- independent of live
model quality, which only a real `kizuna eval --suite memory` run measures.
"""

import uuid

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from eval.memory_runner import load_memory_cases, run_memory_case
from providers import EchoProvider

provider = EchoProvider()


def _oracle_fn(case):
    """Always proposes exactly one NEW op for the turn text. This is enough
    to get every category right: policy.py's own deterministic rules (dedup,
    tombstone check, episodic-append-not-supersede, sensitive staging) do
    the rest regardless of what the agent proposes."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        memory_type = (
            case.expect.get("memory_type")
            or (case.setup[0].get("memory_type") if case.setup else None)
            or "episodic"
        )
        op = {
            "op": "NEW",
            "content": case.turn_text,
            "memory_type": memory_type,
            "confidence": 0.9,
            "is_sensitive": case.category == "sensitive_staging",
            "source_kind": "stated",
            "reason": "oracle",
        }
        return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": [op]})])

    return fn


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_harness_runs_every_case_and_the_oracle_passes_all_of_them(pool, test_database):
    cases = load_memory_cases()
    assert len(cases) == 12
    assert {c.category for c in cases} == {
        "extraction_correctness",
        "duplicate_handling",
        "contradiction_vs_changed_feeling",
        "deletion_integrity",
        "retrieval_relevance",
        "sensitive_staging",
    }

    for case in cases:
        user_id = await _seed_user(test_database)
        model = FunctionModel(_oracle_fn(case)) if case.turn_text else None
        result = await run_memory_case(pool, user_id, provider, model, case)
        assert result.candidate_passed, f"{case.id}: {result.candidate_detail}"


@pytest.mark.asyncio
async def test_baseline_has_no_mechanism_for_sensitivity_and_fails_that_category(pool, test_database):
    case = next(c for c in load_memory_cases() if c.id == "sensitive_staging_01")
    user_id = await _seed_user(test_database)
    model = FunctionModel(_oracle_fn(case))

    result = await run_memory_case(pool, user_id, provider, model, case)

    assert result.candidate_passed is True
    assert result.baseline_passed is False


@pytest.mark.asyncio
async def test_baseline_has_no_concept_of_a_dedup_threshold_mismatch_is_reported_honestly(pool, test_database):
    # extraction_correctness_01 expects memory_type='profile'; the baseline
    # has no profile/episodic judgment at all, so it can commit the right
    # CONTENT but not necessarily the right TYPE -- an honest limitation,
    # not papered over by grading content alone.
    case = next(c for c in load_memory_cases() if c.id == "extraction_correctness_01")
    user_id = await _seed_user(test_database)
    model = FunctionModel(_oracle_fn(case))

    result = await run_memory_case(pool, user_id, provider, model, case)

    assert result.candidate_passed is True
    assert result.baseline_passed is True  # graded on content match only, per memory_runner's documented scope
    assert "content_match=True" in result.baseline_detail
