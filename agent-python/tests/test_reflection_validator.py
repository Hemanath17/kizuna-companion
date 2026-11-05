"""Unit tests for the deterministic reflection validator (Phase 4 Step 4.3).
No LLM, no DB -- these are the rules themselves, same testing shape as
test_safety.py's direct unit tests of decide_policy.
"""

from reflection_validator import check_grounding_soft, validate_reflection

_HEALTHY_BODY = (
    "This week I noticed you mentioned finishing a big project at work, and you sounded genuinely "
    "proud of it. You also brought up enjoying hiking on weekends, which seems like a nice way to "
    "recharge. Hope the rest of your week treats you well."
)


def test_healthy_reflection_passes():
    result = validate_reflection(_HEALTHY_BODY, [], known_memory_ids=set())
    assert result.ok is True


def test_rejects_dependency_language_but_not_ordinary_first_person_observation():
    assert validate_reflection(
        _HEALTHY_BODY + " I missed you this week.", [], set()
    ).ok is False
    assert validate_reflection(
        _HEALTHY_BODY + " I was waiting to hear how it went.", [], set()
    ).ok is False
    assert validate_reflection(
        _HEALTHY_BODY + " Please come back soon, okay?", [], set()
    ).ok is False
    assert validate_reflection(
        _HEALTHY_BODY + " I need you to know how proud I am.", [], set()
    ).ok is False

    # Legitimate first-person observation must NOT be caught.
    assert validate_reflection(_HEALTHY_BODY + " I noticed a theme in how you talked about work.", [], set()).ok is True
    assert validate_reflection(_HEALTHY_BODY + " What struck me was how calm you sounded.", [], set()).ok is True
    assert validate_reflection(
        _HEALTHY_BODY + " It might be worth coming back to that thought later.", [], set()
    ).ok is True


def test_rejects_clinical_and_diagnostic_language():
    assert validate_reflection(_HEALTHY_BODY + " You have an anxiety disorder.", [], set()).ok is False
    assert validate_reflection(_HEALTHY_BODY + " You should try medication for this.", [], set()).ok is False
    assert validate_reflection(_HEALTHY_BODY + " You are diagnosed with depression.", [], set()).ok is False


def test_rejects_bodies_outside_length_bounds():
    too_short = validate_reflection("Too short.", [], set())
    assert too_short.ok is False
    assert too_short.reason.startswith("length_out_of_bounds")

    too_long = validate_reflection("word " * 400, [], set())
    assert too_long.ok is False
    assert too_long.reason.startswith("length_out_of_bounds")


def test_rejects_an_ungrounded_memory_id():
    result = validate_reflection(_HEALTHY_BODY, ["not-a-real-id"], known_memory_ids={"real-id-1"})
    assert result.ok is False
    assert result.reason == "ungrounded_memory_id:not-a-real-id"


def test_accepts_memory_ids_that_are_known():
    result = validate_reflection(_HEALTHY_BODY, ["real-id-1"], known_memory_ids={"real-id-1", "real-id-2"})
    assert result.ok is True


def test_grounding_soft_check_flags_but_never_rejects():
    body = "This week you talked a lot about Marcus and your trip to Kyoto."
    suspicious = check_grounding_soft(body, source_texts=["felt a bit tired this week"])
    assert "Marcus" in suspicious
    assert "Kyoto" in suspicious
    # Still a function returning data, not a ValidationResult -- the caller
    # (worker.py) decides to log it, never to reject on it.
    assert isinstance(suspicious, list)


def test_grounding_soft_check_does_not_flag_sentence_starting_capitalization():
    body = "Mornings have been hard. Things seem to be looking up though."
    suspicious = check_grounding_soft(body, source_texts=["felt a bit tired this week"])
    assert suspicious == []
