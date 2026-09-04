from rag.embeddings import embed_text
from rag.qdrant_client import search_reference_corpus

TEST_QUERIES = [
    "what can I do about anxiety before bed",
    "how is stress different from anxiety",
    "self-care ideas when feeling overwhelmed",
    "when should I actually see a professional",
    "how do I support a friend who's grieving",
]

def run_smoke_test():
    for query in TEST_QUERIES:
        print(f"\nQuery: {query}")
        vector = embed_text(query)
        hits = search_reference_corpus(vector, top_k=3)
        if not hits:
            print("  (no results returned at all)")
            continue
        for i, hit in enumerate(hits, 1):
            print(f"  {i}. [{hit.payload['source']} — {hit.payload['heading']}] "
                  f"(score: {hit.score:.3f})")
            print(f"     {hit.payload['text'][:150]}...")

if __name__ == "__main__":
    run_smoke_test()