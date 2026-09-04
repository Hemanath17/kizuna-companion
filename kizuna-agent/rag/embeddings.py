from sentence_transformers import SentenceTransformer

from config import EMBEDDING_MODEL

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def embed_text(text: str) -> list[float]:
    """Embed a single string with BGE-M3 (1024-dim, L2-normalized)."""
    vector = _get_model().encode(text, normalize_embeddings=True)
    return vector.tolist()
