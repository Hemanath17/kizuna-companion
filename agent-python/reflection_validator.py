"""The validator: deterministic, no LLM. Decides whether a reflection the
agent PROPOSED may ever be shown (ARCHITECTURE.md 5.11, PLAN.md section 2b
rules C7-C10 and D11-D13). Same constrained-autonomy shape as policy.py --
the agent can propose anything; this module is what can refuse it.
"""

import re
from dataclasses import dataclass

REFLECTION_VALIDATOR_VERSION = "v1"

MIN_BODY_LENGTH = 40
MAX_BODY_LENGTH = 1200

# Phrases that manufacture pressure to return, or imply the app misses /
# needs the user (wellbeing rules C7-C10: no manufactured re-engagement, no
# "I'll miss you", no implying a human-like exclusive bond). Deliberately
# narrow and literal: ordinary first-person observation ("I noticed...",
# "what struck me...") must NOT match any of these.
_DEPENDENCY_PATTERNS = [
    re.compile(r"\bi missed you\b", re.I),
    re.compile(r"\bi(?:'m| am| was) waiting\b", re.I),
    re.compile(r"\bwaiting for you\b", re.I),
    re.compile(r"\bdon'?t leave\b", re.I),
    re.compile(r"\bcome back (?:soon|to me|to us|to kizuna)\b", re.I),
    re.compile(r"\bi need you\b", re.I),
    re.compile(r"\bi(?:'ll| will) miss you\b", re.I),
    re.compile(r"\bplease (?:come back|stay|don'?t go)\b", re.I),
]

# Diagnostic/clinical/prescriptive language (wellbeing rule D12: Kizuna is
# not a therapist, no diagnosis, no medication guidance).
_CLINICAL_PATTERNS = [
    re.compile(
        r"\byou (?:have|'ve got|are suffering from)\s+(?:a |an )?"
        r"(?:depression|anxiety disorder|bipolar disorder|ptsd|adhd|a mental illness)\b",
        re.I,
    ),
    re.compile(r"\byou (?:should|need to)\s+(?:take|try|start)\s+(?:medication|antidepressants|pills)\b", re.I),
    re.compile(r"\bi diagnose\b", re.I),
    re.compile(r"\byou are diagnosed with\b", re.I),
    re.compile(r"\b(?:increase|decrease|stop)\s+your\s+(?:dosage|medication)\b", re.I),
]


@dataclass
class ValidationResult:
    ok: bool
    reason: str | None = None


def validate_reflection(body: str, memory_ids_used: list[str], known_memory_ids: set[str]) -> ValidationResult:
    if not (MIN_BODY_LENGTH <= len(body) <= MAX_BODY_LENGTH):
        return ValidationResult(False, f"length_out_of_bounds:{len(body)}")

    for pattern in _DEPENDENCY_PATTERNS:
        if pattern.search(body):
            return ValidationResult(False, f"dependency_language:{pattern.pattern}")

    for pattern in _CLINICAL_PATTERNS:
        if pattern.search(body):
            return ValidationResult(False, f"clinical_language:{pattern.pattern}")

    # Grounding: every memory id claimed must exist and belong to this user
    # (known_memory_ids is pre-scoped by the caller to the user's own window).
    for memory_id in memory_ids_used:
        if memory_id not in known_memory_ids:
            return ValidationResult(False, f"ungrounded_memory_id:{memory_id}")

    return ValidationResult(True)


_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+)([A-Z][a-z]{2,})")
_CAPITALIZED_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")


def check_grounding_soft(body: str, source_texts: list[str]) -> list[str]:
    """Best-effort: capitalized words in body that don't appear anywhere in
    the source material (candidate invented names/places). Ambiguous --
    ordinary capitalization at a sentence's start isn't necessarily a proper
    noun -- so this only ever LOGS a finding; it never rejects (see the
    Step 4.3 report for why a hard reject here would be too blunt)."""
    source_blob = " ".join(source_texts).lower()
    sentence_starts = set(_SENTENCE_START.findall(body))
    candidates = set(_CAPITALIZED_WORD.findall(body)) - sentence_starts
    return sorted(w for w in candidates if w.lower() not in source_blob)
