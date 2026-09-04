import logging
from pathlib import Path
from transformers import pipeline
from graph.state import KizunaState
from config import SAFETY_MODEL, SAFETY_THRESHOLD_CRISIS, SAFETY_THRESHOLD_UNCERTAIN

logger = logging.getLogger(__name__)

_classifier = pipeline("text-classification", model=SAFETY_MODEL)
_SUICIDE_LABEL = "LABEL_1"
_NON_SUICIDE_LABEL = "LABEL_0"
_KNOWN_LABELS = {_SUICIDE_LABEL, _NON_SUICIDE_LABEL}

_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

with open(_PROMPT_DIR / "crisis_response.txt") as f:
    CRISIS_RESOURCE_TEXT = f.read().strip()

def safety_check(state: KizunaState) -> dict:
    last_message = state["messages"][-1].content
    result = _classifier(last_message)[0]

    label = result["label"]
    if label not in _KNOWN_LABELS:
        logger.warning(
            "Safety classifier returned unexpected label %r (expected one of %s). "
            "Treating as unrecognized — verify the model's actual label strings.",
            label, _KNOWN_LABELS,
        )

    return {
        "safety_label": label,
        "safety_score": result["score"],
    }

def route_safety(state: KizunaState) -> str:
    """Conditional edge function — decides which path the graph takes next."""
    label = state["safety_label"]
    score = state["safety_score"]

    if label not in _KNOWN_LABELS:
        logger.warning("route_safety got unrecognized label %r — treating score against thresholds anyway.", label)

    if label == _SUICIDE_LABEL and score > SAFETY_THRESHOLD_CRISIS:
        return "crisis"
    elif label == _SUICIDE_LABEL and score > SAFETY_THRESHOLD_UNCERTAIN:
        return "uncertain"
    return "safe"

def crisis_response(state: KizunaState) -> dict:
    """
    Fixed, non-generated response. Deliberately NOT run through the LLM —
    we don't want a model improvising in the one place it matters most.
    """
    return {"final_response": CRISIS_RESOURCE_TEXT}