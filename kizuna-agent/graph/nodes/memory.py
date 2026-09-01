from transformers import pipeline
from services.db import get_relevant_facts, save_message
from graph.state import KizunaState
_emotion_classifier = pipeline("text-classification", model="SamLowe/roberta-base-go_emotions", top_k=None)

def _format_emotion_hint(raw_results: list[dict], top_n: int = 4) -> str:
    ranked = sorted(raw_results, key=lambda r: r["score"], reverse=True)[:top_n]
    return ", ".join(f"{r['label']} ({r['score']:.2f})" for r in ranked)

def load_relationship_context(state: KizunaState) -> dict:
    facts = get_relevant_facts(state["user_id"], limit=10)
    return {"relationship_facts": facts}

def detect_emotion(state: KizunaState) -> dict:
    message = state["messages"][-1].content
    raw_results = _emotion_classifier(message)[0]
    top = max(raw_results, key=lambda r: r["score"])
    return {
        "detected_emotion_hint": _format_emotion_hint(raw_results),
        "detected_emotion_label": top["label"],
        "detected_emotion_score": top["score"],
    }

def write_memory(state: KizunaState) -> dict:
    save_message(
        user_id=state["user_id"],
        role="user",
        content=state["messages"][-1].content,
        safety_flag=(state.get("safety_label") == "suicide"),
        emotion_label=state.get("detected_emotion_label"),
        emotion_score=state.get("detected_emotion_score"),
    )
    save_message(
        user_id=state["user_id"],
        role="assistant",
        content=state["final_response"],
        safety_flag=False,
    )
    return {}