from langgraph.graph import StateGraph, END

from graph.state import KizunaState
from graph.nodes.safety import safety_check, route_safety, crisis_response
from graph.nodes.memory import load_relationship_context, detect_emotion, write_memory
from graph.nodes.router import condense_query, route_retrieval, route_retrieval_edge
from graph.nodes.retrieval import retrieve_context, skip_retrieval
from graph.nodes.generation import generate_response
from graph.nodes.extraction import extract_facts

def build_kizuna_graph():
    graph = StateGraph(KizunaState)

    graph.add_node("safety_check", safety_check)
    graph.add_node("crisis_response", crisis_response)
    graph.add_node("load_relationship_context", load_relationship_context)
    graph.add_node("detect_emotion", detect_emotion)
    graph.add_node("condense_query", condense_query)
    graph.add_node("route_retrieval", route_retrieval)
    graph.add_node("retrieve_context", retrieve_context)
    graph.add_node("skip_retrieval", skip_retrieval)
    graph.add_node("generate_response", generate_response)
    graph.add_node("extract_facts", extract_facts)
    graph.add_node("write_memory", write_memory)

    graph.set_entry_point("safety_check")

    # safety branch 
    graph.add_conditional_edges(
        "safety_check",
        route_safety,
        {
            "crisis": "crisis_response",
            "uncertain": "load_relationship_context",
            "safe": "load_relationship_context",
        },
    )
    graph.add_edge("crisis_response", "write_memory")
    graph.add_edge("load_relationship_context", "detect_emotion")
    graph.add_edge("detect_emotion", "condense_query")
    graph.add_edge("condense_query", "route_retrieval")
    graph.add_conditional_edges(
        "route_retrieval",
        route_retrieval_edge,
        {
            "retrieve": "retrieve_context",
            "skip": "skip_retrieval",
        },
    )
    graph.add_edge("retrieve_context", "generate_response")
    graph.add_edge("skip_retrieval", "generate_response")
    graph.add_edge("generate_response", "extract_facts")
    graph.add_edge("extract_facts", "write_memory")
    graph.add_edge("write_memory", END)

    return graph.compile()
kizuna_graph = build_kizuna_graph()
