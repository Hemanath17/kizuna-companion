"""The Phase 0 vertical-slice acceptance test (PLAN.md Step 3.4): a fact is
remembered, the user corrects it in a later turn, and a still-later turn's
retrieval sees only the correction -- the old fact never resurfaces.

Drives the real pipeline end to end: memory_agent (propose) -> policy
(commit, with provenance) -> context_builder (retrieve). The correction
turn's oracle genuinely uses search_memory_tool to find the fact it's
correcting, the same way a real model would.
"""

import uuid
from datetime import datetime, timezone

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import memory_store
from context_builder import select_memories
from memory_agent import propose_memory_ops
from policy import commit_proposed_operations
from providers import EchoProvider

provider = EchoProvider()

ORIGINAL_FACT = "has a big interview Friday at Acme"
CORRECTED_FACT = "has a big interview Thursday at Acme"


def _extraction_oracle(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    out_tool = info.output_tools[0]
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=out_tool.name,
                args={
                    "response": [
                        {
                            "op": "NEW",
                            "content": ORIGINAL_FACT,
                            "memory_type": "episodic",
                            "confidence": 0.9,
                            "is_sensitive": False,
                            "source_kind": "stated",
                            "reason": "user stated it directly",
                        }
                    ]
                },
            )
        ]
    )


def _correction_oracle(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool_returns = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
    if not tool_returns:
        return ModelResponse(parts=[ToolCallPart(tool_name="search_memory_tool", args={"query_text": "interview"})])

    candidates = tool_returns[-1].content
    target = next(c for c in candidates if "interview" in c["content"])
    out_tool = info.output_tools[0]
    return ModelResponse(
        parts=[
            ToolCallPart(
                tool_name=out_tool.name,
                args={
                    "response": [
                        {
                            "op": "UPDATE",
                            "target_id": target["id"],
                            "content": CORRECTED_FACT,
                            "memory_type": "episodic",
                            "confidence": 0.95,
                            "is_sensitive": False,
                            "source_kind": "stated",
                            "reason": "user corrected the day",
                        }
                    ]
                },
            )
        ]
    )


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_correction_sticks_and_the_old_fact_never_resurfaces_in_retrieval(pool, test_database):
    user_id = await _seed_user(test_database)

    # Turn 1: "I have a big interview Friday at Acme" -- remembered.
    proposals = await propose_memory_ops(
        pool, user_id, provider, "I have a big interview Friday at Acme", model=FunctionModel(_extraction_oracle)
    )
    decisions = await commit_proposed_operations(
        pool, user_id, provider, proposals, turn_timestamp=datetime.now(timezone.utc)
    )
    assert decisions[0].accepted is True
    original_id = decisions[0].memory_id

    memories = await memory_store.list_memories(pool, user_id)
    assert any(m["content"] == ORIGINAL_FACT and m["status"] == "active" for m in memories)

    # Turn 2: "actually it's Thursday" -- the agent searches, finds the
    # original fact, and proposes a correction; the policy layer supersedes
    # it with provenance intact.
    proposals = await propose_memory_ops(
        pool, user_id, provider, "actually it's Thursday", model=FunctionModel(_correction_oracle)
    )
    decisions = await commit_proposed_operations(
        pool, user_id, provider, proposals, turn_timestamp=datetime.now(timezone.utc)
    )
    assert decisions[0].accepted is True
    corrected_id = decisions[0].memory_id

    original = await memory_store.get_memory(pool, user_id, original_id)
    assert original["status"] == "superseded"
    assert str(original["superseded_by"]) == corrected_id

    # Turn 3 (later): retrieval for a related query sees Thursday/Acme, and
    # the old Friday fact never resurfaces -- superseded rows are excluded
    # by context_builder's reliance on memory_store's default active-only
    # search.
    retrieved = await select_memories(pool, user_id, provider, "when's my interview again, Friday or what?")

    assert any("Thursday" in m["content"] and "Acme" in m["content"] for m in retrieved)
    assert not any("Friday" in m["content"] for m in retrieved)
