from pathlib import Path

from services.groq_client import main_completion
from graph.nodes.router import format_history
from graph.state import KizunaState

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

with open(_PROMPT_DIR / "chat_system_prompt.txt") as f:
    SYSTEM_PROMPT_TEMPLATE = f.read()

DRIFT_REFRESH_EVERY = 6  
_DRIFT_REMINDER = (
    "\n\nREMINDER — check these before replying: 1-3 sentences, casual, lowercase ok. "
    "No advice unless asked. No clichés. Don't repeat phrasings you've already used. "
    "If your recent replies have gotten longer or more formal, pull back now."
)

def generate_response(state: KizunaState) -> dict:
    relationship_context = ""
    if state.get("relationship_facts"):
        facts = "\n".join(f"- {f}" for f in state["relationship_facts"])
        relationship_context = f"Things you know about this person:\n{facts}\n"

    emotion_hint = ""
    if state.get("detected_emotion_hint"):
        emotion_hint = (
            f"Their message shows this emotional signal (label, confidence): "
            f"{state['detected_emotion_hint']}. Use this mix to judge their tone — "
            f"don't just react to the single strongest word if the others shift the "
            f"picture (e.g. multiple negative signals together suggest they're "
            f"overwhelmed, not just one thing).\n"
        )

    grounding_context = ""
    if state.get("retrieved_context"):
        chunks = "\n---\n".join(state["retrieved_context"])
        grounding_context = (
            f"If relevant, ground any suggestion in this reference content "
            f"(don't just repeat it verbatim, weave it in naturally):\n{chunks}\n"
        )
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        relationship_context=relationship_context,
        emotion_hint=emotion_hint,
        grounding_context=grounding_context,
    )
    # Softened path for "uncertain" safety scores — extra care, still generated.
    if state.get("safety_label") == "suicide" and 0.3 < state.get("safety_score", 0) <= 0.6:
        system_prompt += (
            "\nBe extra gentle and check in directly on how they're doing "
            "emotionally right now, without being alarmist."
        )

    turn_count = len([m for m in state["messages"] if m.type == "human"])
    if turn_count > 0 and turn_count % DRIFT_REFRESH_EVERY == 0:
        system_prompt += _DRIFT_REMINDER

    history = format_history(state["messages"])
    response = main_completion(system_prompt=system_prompt, history=history)

    return {"final_response": response}
