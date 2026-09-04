from groq import Groq
from config import GROQ_API_KEY, GROQ_MAIN_MODEL, GROQ_FAST_MODEL
_client = Groq(api_key=GROQ_API_KEY)

def fast_completion(prompt: str, json_mode: bool = False) -> str:
    """Cheap/fast calls: condensation, routing, extraction, emotion detection."""
    kwargs = {}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    response = _client.chat.completions.create(
        model=GROQ_FAST_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        **kwargs,
    )
    return response.choices[0].message.content

def main_completion(system_prompt: str, history: str) -> str:
    """The actual user-facing generation call."""
    response = _client.chat.completions.create(
        model=GROQ_MAIN_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": history},
        ],
        temperature=0.8,
    )
    return response.choices[0].message.content