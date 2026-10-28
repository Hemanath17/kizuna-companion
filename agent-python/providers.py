import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class ProviderInterface(ABC):
    @abstractmethod
    def generate(self, text: str) -> AsyncIterator[str]:
        """Yield the reply token by token. Real LLM providers plug in here later."""


class EchoProvider(ProviderInterface):
    async def generate(self, text: str) -> AsyncIterator[str]:
        reply = f"Got it — you said: {text}"
        for word in reply.split(" "):
            yield word + " "
            await asyncio.sleep(0.01)
