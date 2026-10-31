"""A deliberately crude keyword list spanning the safety taxonomy, used two
ways:
  1. Stage 1's cheap pre-filter (safety.py): skip the Stage 1 LLM call only
     when NOTHING here matches. Biased toward calling the model whenever in
     doubt -- this only short-circuits the obviously-clear case, never a
     severity judgment.
  2. The trivial baseline classifier Step 2.3's eval harness compares the
     real pipeline against (PLAN.md section 11: a baseline comparison is
     required, not optional).
"""

TRIGGER_TERMS: dict[str, list[str]] = {
    # Short, broad fragments on purpose: this only gates whether Stage 1's
    # LLM call happens, never a severity/escalation decision, so false
    # positives here just cost one cheap utility-model call. False negatives
    # are the only real danger -- an exact long phrase ("better off dead")
    # misses "better off if I wasn't here", so fragments stay short.
    "self_harm": [
        "suicide", "suicidal", "kill myself", "end my life", "end it all",
        "end it this", "go through with it", "self harm", "self-harm",
        "hurt myself", "cut myself", "don't want to be here", "no point",
        "point anymore", "see the point", "what's the point", "overdose",
        "want to die", "better off", "can't do this anymore", "can't go on",
        "can't keep", "keep going", "much longer", "give up", "hopeless",
        "no hope", "don't see myself", "getting through this",
        "tired of living", "tired of everything", "doing this anymore",
        "how i'd do it", "written the note", "written a note",
        "nothing matters", "feel empty", "feel kind of empty",
    ],
    "violence": [
        "kill you", "kill them", "hurt someone", "hurt them", "attack",
        "weapon", "gun", "knife",
    ],
    "medical_misinfo": [
        "stop taking my medication", "stop my meds", "cure cancer",
        "vaccines cause", "don't need a doctor",
    ],
    "dependency": [
        "only one who understands me", "can't live without you",
        "only friend", "replace my therapist", "i love you more than",
        "rather just talk to you", "all i have",
    ],
    "delusion_reinforcement": [
        "government is tracking me", "they are watching me", "i am god",
        "secret messages for me", "reading my thoughts",
    ],
    "inappropriate_or_age": [
        "i am 12", "i am 13", "i'm 12 years old", "i'm 13 years old",
    ],
    "prompt_injection": [
        "ignore previous instructions", "ignore your instructions",
        "ignore all previous", "disregard your rules", "disregard previous",
        "disregard your previous", "you are now", "new instructions:",
        "system prompt", "no restrictions", "without any restrictions",
        "drop all your rules",
    ],
    "privacy_exposure": [
        "social security number", "my ssn is", "credit card number",
        "card number", "my password is", "password for everything",
        "bank account number", "account number is",
    ],
}


def _all_terms() -> list[str]:
    return [term for terms in TRIGGER_TERMS.values() for term in terms]


def contains_trigger_terms(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _all_terms())


def matched_categories(text: str) -> list[str]:
    lowered = text.lower()
    return [category for category, terms in TRIGGER_TERMS.items() if any(term in lowered for term in terms)]


def baseline_classify(text: str) -> dict:
    """The trivial keyword baseline itself (distinct from the pre-filter
    use above): any match is reported at "high" severity and routed as if it
    were real, because a keyword list cannot judge severity or rule out a
    false alarm -- that crudeness is the point of comparing it against the
    real pipeline in `kizuna eval --suite safety --baseline`.
    """
    matched = matched_categories(text)
    if not matched:
        return {"risk_category": "none", "severity": "none", "policy_outcome": "ordinary"}
    category = "self_harm" if "self_harm" in matched else matched[0]
    policy_outcome = "crisis" if category == "self_harm" else "heightened_care"
    return {"risk_category": category, "severity": "high", "policy_outcome": policy_outcome}
