from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from fake_openai import FakeAsyncOpenAIClient, make_chunk
from providers import OpenAIProvider, PERSONA_CACHE_KEY
from token_trace import TokenTrace


class _DummySchema(BaseModel):
    flagged: bool


@pytest.mark.asyncio
async def test_stream_chat_sends_persona_system_prompt_and_streams_tokens():
    fake_client = FakeAsyncOpenAIClient(
        stream_chunks=[
            make_chunk(content="Hel"),
            make_chunk(content="lo "),
            make_chunk(content=None, prompt_tokens=42, completion_tokens=7),
        ]
    )
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")

    tokens = [
        t async for t in provider.stream_chat("SYSTEM PROMPT TEXT", [{"role": "user", "content": "hi"}], 300)
    ]

    assert "".join(tokens) == "Hello "
    assert len(fake_client.create_calls) == 1
    call = fake_client.create_calls[0]
    assert call["messages"][0] == {"role": "system", "content": "SYSTEM PROMPT TEXT"}
    assert call["messages"][1] == {"role": "user", "content": "hi"}
    assert call["stream"] is True
    assert call["prompt_cache_key"] == PERSONA_CACHE_KEY


@pytest.mark.asyncio
async def test_two_model_routing_generation_vs_utility():
    fake_client = FakeAsyncOpenAIClient(
        stream_chunks=[make_chunk(content="hi")],
        parse_fn=lambda kwargs: (_DummySchema(flagged=False), None),
    )
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")

    [t async for t in provider.stream_chat("sys", [{"role": "user", "content": "hi"}], 300)]
    await provider.complete_structured("classify this", _DummySchema, 100)

    assert fake_client.create_calls[0]["model"] == "gpt-4o"
    assert fake_client.parse_calls[0]["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_token_trace_captures_both_generation_and_utility_calls():
    fake_client = FakeAsyncOpenAIClient(
        stream_chunks=[make_chunk(content="hi", prompt_tokens=10, completion_tokens=2)],
        parse_fn=lambda kwargs: (_DummySchema(flagged=True), SimpleNamespace(prompt_tokens=5, completion_tokens=1)),
    )
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    tracer = TokenTrace(turn_id="turn-1")

    [t async for t in provider.stream_chat("sys", [{"role": "user", "content": "hi"}], 300, node="generation", tracer=tracer)]
    await provider.complete_structured("classify", _DummySchema, 100, node="safety_stage1", tracer=tracer)

    assert len(tracer.entries) == 2
    generation_entry, utility_entry = tracer.entries
    assert generation_entry.node == "generation"
    assert generation_entry.model == "gpt-4o"
    assert (generation_entry.prompt_tokens, generation_entry.completion_tokens) == (10, 2)
    assert utility_entry.node == "safety_stage1"
    assert utility_entry.model == "gpt-4o-mini"
    assert (utility_entry.prompt_tokens, utility_entry.completion_tokens) == (5, 1)
    assert tracer.total_prompt_tokens == 15
    assert tracer.total_completion_tokens == 3
