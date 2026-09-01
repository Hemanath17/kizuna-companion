# Only runs when route_retrieval_edge sent us here
from rag.qdrant_client import search_reference_corpus
from rag.embeddings import embed_text
from graph.state import KizunaState
RELEVANCE_THRESHOLD = 0.5  

def retrieve_context(state: KizunaState) -> dict:
    query = state["standalone_query"]
    query_vector = embed_text(query)

    hits = search_reference_corpus(query_vector, top_k=3)
    relevant_hits = [h for h in hits if h.score > RELEVANCE_THRESHOLD]
    chunks = [hit.payload["text"] for hit in relevant_hits]

    return {"retrieved_context": chunks}  # legitimately empty if nothing scores well

def skip_retrieval(state: KizunaState) -> dict:
    return {"retrieved_context": []}