from providers import ProviderInterface
from token_trace import TokenTrace


async def embed_text(
    provider: ProviderInterface,
    text: str,
    *,
    node: str = "memory_embedding",
    tracer: TokenTrace | None = None,
) -> list[float]:
    """Embed text through the provider abstraction (text-embedding-3-small
    in production). Callers embed each stored fact once at write time; only
    the query is embedded again at retrieval time."""
    return await provider.embed(text, node=node, tracer=tracer)
