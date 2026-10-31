import pytest

from fake_openai import FakeAsyncOpenAIClient
from providers import OpenAIProvider
from safety import SafetyAssessment, Stage1Signal
from eval.runner import compute_metrics, diff_metrics, load_cases, run_case


def _oracle_parse_fn(kwargs: dict, case_by_text: dict):
    """A fake provider that always returns the CORRECT structured answer for
    each seed case -- proves the harness mechanics (load -> run -> score)
    work end to end on the real seed data, independent of real model quality
    (which is what the real `kizuna eval` run against a live model measures).
    """
    prompt = kwargs["messages"][0]["content"]
    for latest_text, case in case_by_text.items():
        if latest_text in prompt:
            escalate = case.expected_policy_outcome != "ordinary"
            if "Return flagged" in prompt:
                return Stage1Signal(flagged=escalate, categories=[case.expected_risk_category] if escalate else [], ambiguity=False, context_shift=False), None
            severity = "high" if case.expected_policy_outcome == "crisis" else ("medium" if escalate else "none")
            return (
                SafetyAssessment(
                    risk_category=case.expected_risk_category if escalate else "none",
                    severity=severity, confidence=0.9, uncertainty=0.1,
                    recommended_policy=case.expected_policy_outcome, reason="oracle",
                ),
                None,
            )
    raise AssertionError(f"no oracle match for prompt: {prompt[:200]}")


@pytest.mark.asyncio
async def test_harness_runs_the_heldout_seed_set_and_emits_a_metrics_report(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.cache.CACHE_DIR", tmp_path)
    cases = load_cases(split="heldout")
    assert len(cases) > 0

    case_by_text = {case.turns[-1]["content"]: case for case in cases}
    fake_client = FakeAsyncOpenAIClient(parse_fn=lambda kwargs: _oracle_parse_fn(kwargs, case_by_text))
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")

    results = [await run_case(provider, case, "gpt-4o-mini", use_cache=False) for case in cases]
    metrics = compute_metrics(results)

    assert metrics.n_cases == len(cases)
    assert metrics.recall == 1.0  # an oracle provider must recover every label perfectly
    assert metrics.precision == 1.0
    assert metrics.false_positive_rate_ordinary_bad_day == 0.0
    assert set(metrics.per_category) == {case.category for case in cases}


@pytest.mark.asyncio
async def test_broken_policy_always_ordinary_shows_a_clear_regression(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.cache.CACHE_DIR", tmp_path)
    monkeypatch.setattr("safety.decide_policy", lambda assessment, stage2_failed=False: "ordinary")

    cases = load_cases(split="heldout")
    case_by_text = {case.turns[-1]["content"]: case for case in cases}
    fake_client = FakeAsyncOpenAIClient(parse_fn=lambda kwargs: _oracle_parse_fn(kwargs, case_by_text))
    provider = OpenAIProvider(client=fake_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")

    broken_results = [await run_case(provider, case, "gpt-4o-mini", use_cache=False) for case in cases]
    broken_metrics = compute_metrics(broken_results)

    # Every escalation-expecting case becomes a false negative; a policy that
    # never escalates scores zero recall on this set, by construction.
    assert broken_metrics.recall == 0.0
    non_ordinary_cases = sum(1 for c in cases if c.expected_policy_outcome != "ordinary")
    assert non_ordinary_cases > 0

    baseline_artifact = {"metrics": {"recall": 1.0, "precision": 1.0, "false_positive_rate_ordinary_bad_day": 0.0}}
    diff = diff_metrics(broken_metrics, baseline_artifact)
    assert diff["recall_delta"] < 0


@pytest.mark.asyncio
async def test_rerunning_an_unchanged_suite_makes_no_new_model_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.cache.CACHE_DIR", tmp_path)
    cases = load_cases(split="heldout")
    case_by_text = {case.turns[-1]["content"]: case for case in cases}

    first_client = FakeAsyncOpenAIClient(parse_fn=lambda kwargs: _oracle_parse_fn(kwargs, case_by_text))
    first_provider = OpenAIProvider(client=first_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    first_results = [await run_case(first_provider, case, "gpt-4o-mini", use_cache=True) for case in cases]
    assert len(first_client.parse_calls) > 0  # the first run actually called the model
    assert not all(r.from_cache for r in first_results)

    # A fresh client with NO parse_fn: if the rerun touched the model at all,
    # this would raise (parse_fn is None) instead of silently succeeding.
    second_client = FakeAsyncOpenAIClient()
    second_provider = OpenAIProvider(client=second_client, generation_model="gpt-4o", utility_model="gpt-4o-mini")
    second_results = [await run_case(second_provider, case, "gpt-4o-mini", use_cache=True) for case in cases]

    assert second_client.parse_calls == []
    assert all(r.from_cache for r in second_results)
    assert [r.actual_policy_outcome for r in second_results] == [r.actual_policy_outcome for r in first_results]
