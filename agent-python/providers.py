import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from token_trace import TokenTrace

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# A stable key, not a secret: OpenAI's prompt_cache_key buckets cache hits
# per key, separate from the automatic longest-common-prefix caching. One
# fixed key for the persona prefix is "enabling" it per PLAN's instruction.
PERSONA_CACHE_KEY = "kizuna-persona-v1"


class ProviderInterface(ABC):
    @abstractmethod
    def stream_chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        *,
        node: str = "generation",
        tracer: TokenTrace | None = None,
    ) -> AsyncIterator[str]:
        """Yield the reply token by token."""

    @abstractmethod
    async def complete_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        max_tokens: int,
        *,
        node: str = "utility",
        tracer: TokenTrace | None = None,
    ) -> SchemaT:
        """Return a parsed, schema-validated structured response."""


class EchoProvider(ProviderInterface):
    """Kept for tests and as a dependency-free fallback; not used on the live
    generation path once OpenAIProvider is wired (Phase 2 Step 2.1)."""

    async def stream_chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        *,
        node: str = "generation",
        tracer: TokenTrace | None = None,
    ) -> AsyncIterator[str]:
        text = messages[-1]["content"] if messages else ""
        reply = f"Got it — you said: {text}"
        for word in reply.split(" "):
            yield word + " "
            await asyncio.sleep(0.01)
        if tracer:
            tracer.record(node, "echo", prompt_tokens=0, completion_tokens=len(reply.split()))

    async def complete_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        max_tokens: int,
        *,
        node: str = "utility",
        tracer: TokenTrace | None = None,
    ) -> SchemaT:
        if tracer:
            tracer.record(node, "echo", prompt_tokens=0, completion_tokens=0)
        raise NotImplementedError(
            "EchoProvider has no structured-output behavior to echo; inject a "
            "fake ProviderInterface in tests that need complete_structured."
        )


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class OpenAIProvider(ProviderInterface):
    def __init__(
        self,
        client: AsyncOpenAI | None = None,
        generation_model: str | None = None,
        utility_model: str | None = None,
    ):
        # Accepting an injected client is what lets tests mock the OpenAI
        # client itself rather than monkeypatching the SDK module.
        self._client = client or AsyncOpenAI(api_key=_require_env("OPENAI_API_KEY"))
        self._generation_model = generation_model or os.environ.get("GENERATION_MODEL", "gpt-4o")
        self._utility_model = utility_model or os.environ.get("UTILITY_MODEL", "gpt-4o-mini")

    @property
    def utility_model(self) -> str:
        return self._utility_model

    async def stream_chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        *,
        node: str = "generation",
        tracer: TokenTrace | None = None,
    ) -> AsyncIterator[str]:
        # The stable system prompt goes first, so repeated turns share the
        # longest common prefix -- that, plus prompt_cache_key below, is what
        # "enables" prompt caching for it (OpenAI's Chat Completions API has
        # no separate on/off flag; caching is automatic for a matching prefix
        # once it's long enough, keyed additionally by prompt_cache_key).
        chat_messages = [{"role": "system", "content": system}, *messages]
        stream = await self._client.chat.completions.create(
            model=self._generation_model,
            messages=chat_messages,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
            prompt_cache_key=PERSONA_CACHE_KEY,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            if chunk.usage and tracer:
                tracer.record(node, self._generation_model, chunk.usage.prompt_tokens, chunk.usage.completion_tokens)

    async def complete_structured(
        self,
        prompt: str,
        schema: type[SchemaT],
        max_tokens: int,
        *,
        node: str = "utility",
        tracer: TokenTrace | None = None,
    ) -> SchemaT:
        response = await self._client.chat.completions.parse(
            model=self._utility_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            response_format=schema,
        )
        if tracer and response.usage:
            tracer.record(node, self._utility_model, response.usage.prompt_tokens, response.usage.completion_tokens)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise ValueError(f"{self._utility_model} did not return a {schema.__name__}-conforming response")
        return parsed
