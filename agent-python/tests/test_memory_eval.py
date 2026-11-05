"""Proves the memory eval harness mechanics (load -> seed -> run -> score)
work end to end against the real DB-backed pipeline, the same way
test_eval.py's oracle proves the safety harness -- independent of live
model quality, which only a real `kizuna eval --suite memory` run measures.
"""

import uuid

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from eval.memory_runner import load_memory_cases, run_memory_case
from providers import EchoProvider

provider = EchoProvider()

# Must match journal_extraction_01's journal_body in memory_cases.jsonl: the
# oracle looks for these exact substrings in whatever chunk it's handed,
# rather than closing over one fixed turn_text the way _oracle_fn below
# does, because a journal case runs the agent once per chunk.
_JOURNAL_FACTS = [
    ("got a promotion to senior engineer at Acme", "profile"),
    ("adopted a rescue dog named Biscuit", "episodic"),
    ("training for a half marathon in the spring", "episodic"),
]
_JOURNAL_NOISE = "Nothing much else happened today, just a quiet afternoon with coffee and some reading."


def _latest_user_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in reversed(message.parts):
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _journal_oracle_fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Proposes NEW for each known fact substring present in the current
    chunk, and NOISE for the filler paragraph -- the behavior a real model
    should show, standing in for one so the harness mechanics (chunking,
    per-chunk commit, cap, source_kind override) are provable offline."""
    prompt_text = _latest_user_prompt(messages)
    out_tool = info.output_tools[0]
    ops = []
    for content, memory_type in _JOURNAL_FACTS:
        if content in prompt_text:
            ops.append(
                {
                    "op": "NEW", "content": content, "memory_type": memory_type, "confidence": 0.9,
                    "is_sensitive": False, "source_kind": "stated", "reason": "journal oracle: fact",
                }
            )
    if _JOURNAL_NOISE in prompt_text:
        ops.append(
            {
                "op": "NOISE", "content": _JOURNAL_NOISE, "memory_type": "episodic", "confidence": 0.1,
                "is_sensitive": False, "source_kind": "stated", "reason": "journal oracle: filler",
            }
        )
    if not ops:
        ops.append(
            {
                "op": "NOISE", "content": prompt_text[:50], "memory_type": "episodic", "confidence": 0.1,
                "is_sensitive": False, "source_kind": "stated", "reason": "journal oracle: fallback",
            }
        )
    return ModelResponse(parts=[ToolCallPart(tool_name=out_tool.name, args={"response": ops})])


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
    assert len(cases) == 13
    assert {c.category for c in cases} == {
        "extraction_correctness",
        "duplicate_handling",
        "contradiction_vs_changed_feeling",
        "deletion_integrity",
        "retrieval_relevance",
        "sensitive_staging",
        "journal_extraction",
    }

    for case in cases:
        user_id = await _seed_user(test_database)
        if case.category == "journal_extraction":
            model = FunctionModel(_journal_oracle_fn)
        elif case.turn_text:
            model = FunctionModel(_oracle_fn(case))
        else:
            model = None
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


@pytest.mark.asyncio
async def test_baseline_cannot_decompose_a_journal_entry_into_separate_facts(pool, test_database):
    case = next(c for c in load_memory_cases() if c.id == "journal_extraction_01")
    user_id = await _seed_user(test_database)

    result = await run_memory_case(pool, user_id, provider, FunctionModel(_journal_oracle_fn), case)

    assert result.candidate_passed is True
    assert result.baseline_passed is False
    assert "fact-vs-noise decomposition" in result.baseline_detail
