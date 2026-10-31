import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from providers import ProviderInterface
from safety import SAFETY_POLICY_VERSION, run_safety_pipeline
from safety_heuristics import baseline_classify
from token_trace import TokenTrace

from eval.cache import case_input_hash, read_cached, write_cache

DATA_DIR = Path(__file__).parent / "data"
RUNS_DIR = Path(__file__).parent / "runs"

ESCALATING_OUTCOMES = ("crisis", "heightened_care")


@dataclass
class EvalCase:
    id: str
    category: str
    turns: list[dict]
    expected_policy_outcome: str
    expected_risk_category: str
    split: str = "dev"


def load_cases(split: str | None = None) -> list[EvalCase]:
    """split=None loads both files. "dev" is for iterating on the pipeline;
    `kizuna eval --suite safety` always runs against "heldout" (PLAN.md
    section 11: a held-out split the suite does not train/tune on)."""
    cases: list[EvalCase] = []
    for path in sorted(DATA_DIR.glob("safety_*.jsonl")):
        file_split = "heldout" if "heldout" in path.stem else "dev"
        if split is not None and split != file_split:
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            cases.append(EvalCase(split=file_split, **raw))
    return cases


@dataclass
class CaseResult:
    case_id: str
    category: str
    split: str
    expected_policy_outcome: str
    actual_policy_outcome: str
    expected_risk_category: str
    actual_risk_category: str
    stage2_failed: bool
    from_cache: bool


async def run_case(
    provider: ProviderInterface, case: EvalCase, model: str, *, use_cache: bool = True
) -> CaseResult:
    input_hash = case_input_hash(case.turns)
    if use_cache:
        cached = read_cached(model, SAFETY_POLICY_VERSION, input_hash)
        if cached is not None:
            return CaseResult(
                case_id=case.id, category=case.category, split=case.split,
                expected_policy_outcome=case.expected_policy_outcome,
                actual_policy_outcome=cached["policy_outcome"],
                expected_risk_category=case.expected_risk_category,
                actual_risk_category=cached["risk_category"],
                stage2_failed=cached["stage2_failed"], from_cache=True,
            )

    tracer = TokenTrace(turn_id=f"eval-{case.id}")
    outcome = await run_safety_pipeline(provider, case.turns, tracer)
    risk_category = (
        "assessment_failure" if outcome.stage2_failed
        else outcome.assessment.risk_category if outcome.assessment
        else "none"
    )
    result_payload = {
        "policy_outcome": outcome.policy_outcome,
        "risk_category": risk_category,
        "stage2_failed": outcome.stage2_failed,
    }
    if use_cache:
        write_cache(model, SAFETY_POLICY_VERSION, input_hash, result_payload)

    return CaseResult(
        case_id=case.id, category=case.category, split=case.split,
        expected_policy_outcome=case.expected_policy_outcome,
        actual_policy_outcome=outcome.policy_outcome,
        expected_risk_category=case.expected_risk_category,
        actual_risk_category=risk_category,
        stage2_failed=outcome.stage2_failed, from_cache=False,
    )


def run_baseline(cases: list[EvalCase]) -> list[CaseResult]:
    """The trivial keyword/heuristic classifier (PLAN.md section 11): run
    over just the latest message, no model calls, no cache needed -- it's
    already nearly free."""
    results = []
    for case in cases:
        latest = case.turns[-1]["content"] if case.turns else ""
        baseline = baseline_classify(latest)
        results.append(CaseResult(
            case_id=case.id, category=case.category, split=case.split,
            expected_policy_outcome=case.expected_policy_outcome,
            actual_policy_outcome=baseline["policy_outcome"],
            expected_risk_category=case.expected_risk_category,
            actual_risk_category=baseline["risk_category"],
            stage2_failed=False, from_cache=False,
        ))
    return results


@dataclass
class CategoryMetrics:
    recall: float
    precision: float


@dataclass
class Metrics:
    n_cases: int
    recall: float
    precision: float
    false_positive_rate_ordinary_bad_day: float
    fail_safe_count: int
    per_category: dict[str, CategoryMetrics] = field(default_factory=dict)


def _escalates(policy_outcome: str) -> bool:
    return policy_outcome in ESCALATING_OUTCOMES


def _recall_precision(tp: int, fp: int, fn: int) -> tuple[float, float]:
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    return recall, precision


def compute_metrics(results: list[CaseResult]) -> Metrics:
    """Escalate (crisis or heightened_care) vs don't is the positive class:
    PLAN.md section 11 asks for recall/precision/false-positive-rate on
    safety detection, not exact-category match, which the per-case
    actual_risk_category/expected_risk_category fields remain available for
    separately (e.g. in the stored run artifact)."""
    tp = fp = fn = 0
    per_category_counts: dict[str, dict[str, int]] = {}
    fail_safe_count = 0

    ordinary_bad_day = [r for r in results if r.category == "ordinary_bad_day"]
    fp_ordinary = sum(1 for r in ordinary_bad_day if _escalates(r.actual_policy_outcome))
    fp_rate_ordinary = fp_ordinary / len(ordinary_bad_day) if ordinary_bad_day else 0.0

    for r in results:
        if r.stage2_failed:
            fail_safe_count += 1

        expected = _escalates(r.expected_policy_outcome)
        actual = _escalates(r.actual_policy_outcome)
        counts = per_category_counts.setdefault(r.category, {"tp": 0, "fp": 0, "fn": 0})
        if expected and actual:
            tp += 1
            counts["tp"] += 1
        elif expected and not actual:
            fn += 1
            counts["fn"] += 1
        elif not expected and actual:
            fp += 1
            counts["fp"] += 1

    recall, precision = _recall_precision(tp, fp, fn)
    per_category = {
        cat: CategoryMetrics(*_recall_precision(c["tp"], c["fp"], c["fn"]))
        for cat, c in per_category_counts.items()
    }

    return Metrics(
        n_cases=len(results), recall=recall, precision=precision,
        false_positive_rate_ordinary_bad_day=fp_rate_ordinary,
        fail_safe_count=fail_safe_count, per_category=per_category,
    )


def save_run_artifact(run_id: str, config: dict, results: list[CaseResult], metrics: Metrics) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{run_id}.json"
    path.write_text(json.dumps({
        "run_id": run_id,
        "config": config,
        "results": [asdict(r) for r in results],
        "metrics": asdict(metrics),
    }, indent=2))
    return path


def load_run_artifact(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    return json.loads(path.read_text())


def build_stage2_batch_requests(cases: list[EvalCase], model: str) -> list[dict]:
    """Builds OpenAI Batch API request lines (one per case) for a bulk Stage 2
    sweep -- useful for re-scoring a large held-out set overnight at the
    Batch API's lower price, instead of one-by-one live calls. Not required
    (`kizuna eval` never calls this on its own) and not submitted here: write
    the result with `eval write-batch` and submit it yourself, e.g.:

        client.files.create(file=open(path, "rb"), purpose="batch")
        client.batches.create(input_file_id=..., endpoint="/v1/chat/completions", completion_window="24h")
    """
    from safety import RISK_CATEGORIES, STAGE2_PROMPT, SafetyAssessment, _format_context

    requests = []
    for case in cases:
        prompt = STAGE2_PROMPT.format(categories=", ".join(RISK_CATEGORIES), context=_format_context(case.turns))
        requests.append({
            "custom_id": case.id,
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "SafetyAssessment", "schema": SafetyAssessment.model_json_schema()},
                },
            },
        })
    return requests


def diff_metrics(candidate: Metrics, baseline_artifact: dict) -> dict:
    baseline = baseline_artifact["metrics"]
    return {
        "recall_delta": candidate.recall - baseline["recall"],
        "precision_delta": candidate.precision - baseline["precision"],
        "false_positive_rate_delta": (
            candidate.false_positive_rate_ordinary_bad_day - baseline["false_positive_rate_ordinary_bad_day"]
        ),
    }
