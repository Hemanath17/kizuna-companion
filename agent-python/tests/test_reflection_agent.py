import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from embeddings import embed_text
from providers import EchoProvider
from reflection_agent import TOOL_CALL_BUDGET, propose_reflection

provider = EchoProvider()


async def _seed_user(test_database) -> str:
    user_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    await test_database.seed_auth_user(user_id)
    await test_database.seed_profile_and_conversation(user_id, conversation_id)
    return str(user_id)


def _window():
    end = datetime.now(timezone.utc)
    return end - timedelta(days=7), end


@pytest.mark.asyncio
async def test_agent_can_use_both_tools_then_produce_structured_output(pool, test_database):
    user_id = await _seed_user(test_database)
    import memory_store

    embedding = await embed_text(provider, "started a new hobby: painting")
    await memory_store.insert_memory(pool, user_id, memory_type="profile", content="started a new hobby: painting", embedding=embedding)

    calls: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        step = len(calls)
        if step == 0:
            calls.append("memories")
            return ModelResponse(parts=[ToolCallPart(tool_name="list_memories_in_window_tool", args={})])
        if step == 1:
            calls.append("messages")
            return ModelResponse(parts=[ToolCallPart(tool_name="list_messages_in_window_tool", args={"limit": 5})])
        calls.append("final")
        out_tool = info.output_tools[0]
        body = (
            "This week I noticed you picked up painting as a new hobby, which sounds like a nice bit "
            "of creative space for yourself. Hope it keeps being something you enjoy."
        )
        return ModelResponse(
            parts=[ToolCallPart(tool_name=out_tool.name, args={"body": body, "memory_ids_used": []})]
        )

    period_start, period_end = _window()
    output = await propose_reflection(pool, user_id, period_start, period_end, model=FunctionModel(fn))

    assert calls == ["memories", "messages", "final"]
    assert "painting" in output.body
    assert output.memory_ids_used == []


@pytest.mark.asyncio
async def test_agent_tool_call_budget_is_enforced(pool, test_database):
    user_id = await _seed_user(test_database)

    def never_finishes(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart(tool_name="list_memories_in_window_tool", args={})])

    period_start, period_end = _window()
    with pytest.raises(UsageLimitExceeded):
        await propose_reflection(pool, user_id, period_start, period_end, model=FunctionModel(never_finishes))


@pytest.mark.asyncio
async def test_extra_instruction_is_appended_to_the_prompt_for_the_retry(pool, test_database):
    user_id = await _seed_user(test_database)
    seen_prompts: list[str] = []

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        for message in reversed(messages):
            if isinstance(message, ModelRequest):
                for part in reversed(message.parts):
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        seen_prompts.append(part.content)
                        break
                break
        out_tool = info.output_tools[0]
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=out_tool.name,
                    args={
                        "body": "A short, honest, and grounded reflection goes here for the week.",
                        "memory_ids_used": [],
                    },
                )
            ]
        )

    period_start, period_end = _window()
    await propose_reflection(
        pool, user_id, period_start, period_end, model=FunctionModel(fn), extra_instruction="Be more careful this time."
    )

    assert "Be more careful this time." in seen_prompts[0]
