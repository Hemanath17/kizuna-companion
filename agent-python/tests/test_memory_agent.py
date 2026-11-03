import uuid

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from memory_agent import TOOL_CALL_BUDGET, propose_memory_ops
from providers import EchoProvider


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


@pytest.mark.asyncio
async def test_agent_can_use_both_tools_before_proposing(pool, test_database):
    user_id = await _seed_user(test_database)
    calls: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = len(calls)
        if step == 0:
            calls.append("search")
            return ModelResponse(parts=[ToolCallPart(tool_name="search_memory_tool", args={"query_text": "job"})])
        if step == 1:
            calls.append("recent")
            return ModelResponse(parts=[ToolCallPart(tool_name="get_recent_memories_tool", args={"limit": 3})])
        calls.append("final")
        out_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=out_tool.name,
                    args={
                        "response": [
                            {
                                "op": "NEW",
                                "content": "works as a data engineer",
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

    proposals = await propose_memory_ops(
        pool, user_id, EchoProvider(), "I work as a data engineer", model=FunctionModel(fn)
    )

    assert calls == ["search", "recent", "final"]
    assert len(proposals) == 1
    assert proposals[0].op == "NEW"
    assert proposals[0].source_kind == "stated"


@pytest.mark.asyncio
async def test_agent_tool_call_budget_is_enforced(pool, test_database):
    user_id = await _seed_user(test_database)
    call_count = {"n": 0}

    def never_finishes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Always calls a tool again, never produces the final structured output.
        call_count["n"] += 1
        return ModelResponse(parts=[ToolCallPart(tool_name="search_memory_tool", args={"query_text": "x"})])

    with pytest.raises(UsageLimitExceeded):
        await propose_memory_ops(pool, user_id, EchoProvider(), "anything", model=FunctionModel(never_finishes))

    assert call_count["n"] <= TOOL_CALL_BUDGET + 1


@pytest.mark.asyncio
async def test_agent_proposes_noise_when_nothing_durable(pool, test_database):
    user_id = await _seed_user(test_database)

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        out_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=out_tool.name,
                    args={
                        "response": [
                            {
                                "op": "NOISE",
                                "content": "lol",
                                "memory_type": "episodic",
                                "confidence": 0.1,
                                "is_sensitive": False,
                                "source_kind": "stated",
                                "reason": "no durable fact",
                            }
                        ]
                    },
                )
            ]
        )

    proposals = await propose_memory_ops(pool, user_id, EchoProvider(), "lol", model=FunctionModel(fn))

    assert proposals[0].op == "NOISE"
