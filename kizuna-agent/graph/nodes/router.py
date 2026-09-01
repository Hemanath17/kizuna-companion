from turtle import st
from services.groq_client import fast_completion
from graph.state import KizunaState

def condense_query(state: KizunaState) -> dict:
    messages = state["messages"]
    if len(messages) <= 1:
        return {"standalone_query": messages[-1].content}
    history_text = format_history(messages[:-1])
    current = messages[-1].content
    prompt = f"""Given this conversation history:
{history_text}

Rewrite this follow-up message as a standalone question or statement
that includes any necessary context from the history. If it's already
self-contained, return it unchanged.

Follow-up: {current}

Standalone version:"""

    standalone = fast_completion(prompt)
    return {"standalone_query": standalone.strip()}

def route_retrieval(state: KizunaState) -> dict:
    query = state["standalone_query"]

    prompt = f"""Does this message request a coping technique, strategy,
or factual information about managing a feeling (e.g. "what can I do
about my anxiety", "how do I deal with stress")? Or is it casual
conversation, venting, or small talk?

Message: {query}

Answer with exactly one word: "retrieval" or "casual" """

    result = fast_completion(prompt).strip().lower()
    needs_retrieval = result.startswith("retrieval")
    return {"needs_retrieval": needs_retrieval}

def route_retrieval_edge(state: KizunaState) -> str:
    """Conditional edge function."""
    return "retrieve" if state["needs_retrieval"] else "skip"

def format_history(messages: list) -> str:
    lines = []
    for m in messages:
        role = "User" if m.role == "human" else "Kizuna"
        lines.append(f"{role}: {m.content}")
    return "\n".join(lines)