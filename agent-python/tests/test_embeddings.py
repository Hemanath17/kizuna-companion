import pytest

from embeddings import embed_text
from providers import EMBEDDING_DIM, EchoProvider


@pytest.mark.asyncio
async def test_embed_text_is_deterministic_and_matches_embedding_dim():
    provider = EchoProvider()

    first = await embed_text(provider, "I have a big interview Friday at Acme")
    second = await embed_text(provider, "I have a big interview Friday at Acme")

    assert len(first) == EMBEDDING_DIM
    assert first == second
