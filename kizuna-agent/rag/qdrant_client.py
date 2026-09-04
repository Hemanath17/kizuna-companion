from qdrant_client import QdrantClient
from config import QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION

_client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)

def search_reference_corpus(query_vector: list[float], top_k: int = 3):
    response = _client.query_points(
        collection_name=QDRANT_COLLECTION,
        query=query_vector,
        limit=top_k,
    )
    return response.points

def ensure_collection(vector_size: int = 1024):
    """Call once during ingestion setup. BGE-M3 dense output is 1024-dim."""
    from qdrant_client.models import Distance, VectorParams

    if not _client.collection_exists(QDRANT_COLLECTION):
        _client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
