"""A duck-typed stand-in for AsyncOpenAI, built to satisfy exactly the
attribute access OpenAIProvider makes (chunk.choices[0].delta.content,
chunk.usage.prompt_tokens/completion_tokens, response.choices[0].message.parsed,
response.usage). This is what "mock the OpenAI client" means here: inject this
in place of a real AsyncOpenAI, never touch the network.
"""

from collections.abc import Callable
from types import SimpleNamespace


def make_chunk(content: str | None = None, prompt_tokens: int | None = None, completion_tokens: int | None = None):
    choices = [SimpleNamespace(delta=SimpleNamespace(content=content))] if content is not None else []
    usage = (
        SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        if prompt_tokens is not None
        else None
    )
    return SimpleNamespace(choices=choices, usage=usage)


class FakeStream:
    def __init__(self, chunks: list):
        self._chunks = chunks

    def __aiter__(self):
        return self._generate()

    async def _generate(self):
        for chunk in self._chunks:
            yield chunk


class FakeAsyncOpenAIClient:
    """`parse_fn(kwargs) -> (parsed_object, usage_or_None)` lets a test decide
    the structured response per call (e.g. by inspecting kwargs["messages"]
    to tell a Stage 1 call apart from a Stage 2 call), including raising to
    simulate a provider failure.
    """

    def __init__(
        self,
        stream_chunks: list | None = None,
        raise_on_create: Exception | None = None,
        parse_fn: Callable[[dict], tuple[object, object | None]] | None = None,
    ):
        self.stream_chunks = stream_chunks or []
        self.raise_on_create = raise_on_create
        self.parse_fn = parse_fn
        self.create_calls: list[dict] = []
        self.parse_calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create, parse=self._parse))

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return FakeStream(self.stream_chunks)

    async def _parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        parsed, usage = self.parse_fn(kwargs) if self.parse_fn else (None, None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))], usage=usage)
