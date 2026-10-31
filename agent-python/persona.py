from pathlib import Path

_PERSONA_TEMPLATE = (Path(__file__).parent / "prompts" / "persona.txt").read_text()

DEFAULT_CONVERSATION_STYLE = "casual_friend"

CONVERSATION_STYLE_DESCRIPTIONS = {
    "casual_friend": "Casual friend -- relaxed, playful, like texting a close friend.",
    "gentle_listener": "Gentle listener -- soft-spoken, patient, leads with warmth and space.",
    "thoughtful_companion": "Thoughtful companion -- reflective, asks good questions, a little more measured.",
}


def render_persona_prompt(conversation_style: str | None, constraints: str = "") -> str:
    style_key = conversation_style if conversation_style in CONVERSATION_STYLE_DESCRIPTIONS else DEFAULT_CONVERSATION_STYLE
    style_description = CONVERSATION_STYLE_DESCRIPTIONS[style_key]
    return _PERSONA_TEMPLATE.format(conversation_style=style_description, constraints=constraints)
