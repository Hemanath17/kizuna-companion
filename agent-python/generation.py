import asyncio
import logging
from collections.abc import AsyncIterator

from providers import ProviderInterface
from token_trace import TokenTrace

logger = logging.getLogger("kizuna.generation")

GENERATION_MAX_TOKENS = 300
GENERATION_NODE = "generation"

SAFE_FALLBACK_MESSAGE = "Sorry, I'm having a little trouble replying right now. Mind trying again in a moment?"

_RETRY_ATTEMPTS = 2
_RETRY_BACKOFF_SECONDS = 0.5


async def stream_reply_with_fallback(
    provider: ProviderInterface,
    system: str,
    messages: list[dict[str, str]],
    tracer: TokenTrace,
) -> AsyncIterator[str]:
    """Streams the real reply. If the provider fails before yielding a single
    token, retries with backoff, then degrades to a safe static message --
    the turn still gets a reply and is still persisted normally (PLAN: a
    provider error is not the same as a pipeline failure).

    Once real tokens have started streaming, a failure is NOT retried here:
    streamed tokens can't be recalled (PLAN.md section 16 item 9), so a
    mid-stream failure propagates to the caller's existing generic-exception
    handling instead of silently splicing in fallback text.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        agen = provider.stream_chat(system, messages, GENERATION_MAX_TOKENS, node=GENERATION_NODE, tracer=tracer)
        try:
            first_token = await agen.__anext__()
        except StopAsyncIteration:
            return
        except Exception as exc:  # noqa: BLE001 -- any provider failure should trigger the retry/fallback path
            last_exc = exc
            if attempt < _RETRY_ATTEMPTS - 1:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
                continue
            logger.warning("generation_fallback node=%s error=%s", GENERATION_NODE, exc)
            for word in SAFE_FALLBACK_MESSAGE.split(" "):
                yield word + " "
            return

        yield first_token
        async for token in agen:
            yield token
        return

    # Unreachable (the loop always returns), but keeps type-checkers honest
    # about last_exc being used.
    raise last_exc  # pragma: no cover
