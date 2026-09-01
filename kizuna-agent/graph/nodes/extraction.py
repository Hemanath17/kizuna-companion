# Fact extraction from the user messages

import json
from services.groq_client import fast_completion
from services.db import insert_relationship_fact
from graph.state import KizunaState

def extract_facts(state: KizunaState) -> dict:
    message = state["messages"][-1].content

    prompt = f"""Extract any durable fact worth remembering long-term about \
this person (names of people/pets, ongoing situations, preferences, \
relationships). Ignore transient statements (today's mood, one-off events).

Return JSON only: {{"facts": ["...", "..."]}} or {{"facts": []}}

Message: {message}"""

    result = fast_completion(prompt, json_mode=True)
    try:
        facts = json.loads(result).get("facts", [])
    except (json.JSONDecodeError, AttributeError):
        facts = []

    for fact in facts:
        insert_relationship_fact(
            user_id=state["user_id"],
            fact_text=fact,
            source_message_id=None,
        )

    return {} 