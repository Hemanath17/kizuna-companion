import pytest

from fake_openai import FakeAsyncOpenAIClient, make_chunk
from generation import SAFE_FALLBACK_MESSAGE, stream_reply_with_fallback
from providers import OpenAIProvider
from token_trace import TokenTrace


@pytest.mark.asyncio
async def test_provider_failure_falls_back_to_safe_message_after_retrying():
    fake_client = FakeAsyncOpenAIClient(raise_on_create=RuntimeError("boom"))
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    tracer = TokenTrace(turn_id="turn-1")

    tokens = [t async for t in stream_reply_with_fallback(provider, "sys", [{"role": "user", "content": "hi"}], tracer)]

    assert "".join(tokens).strip() == SAFE_FALLBACK_MESSAGE
    # Retried before giving up: two attempts to call the provider.
    assert len(fake_client.create_calls) == 2


@pytest.mark.asyncio
async def test_successful_stream_passes_through_untouched():
    fake_client = FakeAsyncOpenAIClient(stream_chunks=[make_chunk(content="Hel"), make_chunk(content="lo")])
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    tracer = TokenTrace(turn_id="turn-1")

    tokens = [t async for t in stream_reply_with_fallback(provider, "sys", [{"role": "user", "content": "hi"}], tracer)]

    assert "".join(tokens) == "Hello"
    assert len(fake_client.create_calls) == 1
