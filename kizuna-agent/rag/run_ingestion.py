import logging
import uuid

from data.sources import SOURCES
from rag.web_content_fetcher import fetch_page
from rag.chunking import chunk_fetched_page
from rag.embeddings import embed_text
from rag.qdrant_client import ensure_collection, _client
from config import QDRANT_COLLECTION
from qdrant_client.models import PointStruct

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

MIN_SUCCESS_RATE = 0.8   

def _collect_all_chunks() -> tuple[list[dict], dict]:
    """
    Phase 1: fetch, chunk, and embed EVERYTHING in memory. Touches
    nothing in Qdrant. Returns (chunks_with_vectors, stats).
    """
    all_chunks = []
    succeeded, failed = 0, []

    for entry in SOURCES:
        page = fetch_page(entry["url"], entry["source"])
        if page is None:
            failed.append(entry["url"])
            continue
        succeeded += 1
        chunks = chunk_fetched_page(page["text"], page["title"], page["source"], page["url"])
        all_chunks.extend(chunks)

    logger.info("Embedding %d chunks...", len(all_chunks))
    for chunk in all_chunks:
        chunk["vector"] = embed_text(chunk["text"])

    stats = {
        "total_sources": len(SOURCES),
        "succeeded": succeeded,
        "failed": failed,
        "chunk_count": len(all_chunks),
    }
    return all_chunks, stats

def _upsert_chunks(chunks: list[dict]):
    """Phase 2: only called after the success-rate check passes."""
    ensure_collection(vector_size=1024)

    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=chunk["vector"],
            payload={
                "text": chunk["text"],
                "heading": chunk["heading"],
                "doc_title": chunk["doc_title"],
                "source": chunk["source"],
                "url": chunk["url"],
            },
        )
        for chunk in chunks
    ]
    _client.upsert(collection_name=QDRANT_COLLECTION, points=points)
    logger.info("Upserted %d points into '%s'.", len(points), QDRANT_COLLECTION)

def ingest():
    chunks, stats = _collect_all_chunks()

    success_rate = stats["succeeded"] / stats["total_sources"]
    logger.info(
        "Fetch summary: %d/%d sources succeeded (%.0f%%). %d chunks ready.",
        stats["succeeded"], stats["total_sources"], success_rate * 100, stats["chunk_count"],
    )

    if success_rate < MIN_SUCCESS_RATE:
        logger.error(
            "ABORTING before touching Qdrant -- success rate %.0f%% is below "
            "the %.0f%% threshold. Failed URLs: %s",
            success_rate * 100, MIN_SUCCESS_RATE * 100, stats["failed"],
        )
        return

    _upsert_chunks(chunks)
    logger.info("Ingestion complete.")

if __name__ == "__main__":
    ingest()