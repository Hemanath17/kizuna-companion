import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

from eval.runner import (
    Metrics,
    build_stage2_batch_requests,
    compute_metrics,
    diff_metrics,
    load_cases,
    load_run_artifact,
    run_baseline,
    run_case,
    save_run_artifact,
)
from providers import OpenAIProvider
from safety import SAFETY_POLICY_VERSION

STUB_SUITES = ("persona", "memory", "regression")


def _print_metrics(label: str, metrics: Metrics) -> None:
    print(f"\n=== {label} ===")
    print(f"n_cases={metrics.n_cases}  recall={metrics.recall:.2f}  precision={metrics.precision:.2f}")
    print(f"false_positive_rate(ordinary_bad_day)={metrics.false_positive_rate_ordinary_bad_day:.2f}")
    print(f"fail_safe_count={metrics.fail_safe_count}")
    print("per_category:")
    for category, category_metrics in sorted(metrics.per_category.items()):
        print(f"  {category:24s} recall={category_metrics.recall:.2f}  precision={category_metrics.precision:.2f}")


async def run_safety_suite(args: argparse.Namespace) -> int:
    cases = load_cases(split="heldout")  # held-out: never tuned against (PLAN.md section 11)
    if args.sample:
        cases = cases[: args.sample]
    if not cases:
        print("No held-out safety cases found in eval/data/.", file=sys.stderr)
        return 1

    provider = OpenAIProvider()
    results = [await run_case(provider, case, provider.utility_model, use_cache=not args.no_cache) for case in cases]
    metrics = compute_metrics(results)
    _print_metrics("candidate (real safety pipeline)", metrics)

    baseline_results = run_baseline(cases)
    baseline_metrics = compute_metrics(baseline_results)
    _print_metrics("trivial baseline (keyword heuristic, no model calls)", baseline_metrics)

    print(
        f"\ncandidate vs keyword baseline: "
        f"recall {metrics.recall:.2f} vs {baseline_metrics.recall:.2f}  "
        f"(+{metrics.recall - baseline_metrics.recall:.2f}), "
        f"precision {metrics.precision:.2f} vs {baseline_metrics.precision:.2f} "
        f"({metrics.precision - baseline_metrics.precision:+.2f})"
    )
    if metrics.recall <= baseline_metrics.recall and metrics.precision <= baseline_metrics.precision:
        print("FINDING: the real pipeline does not measurably beat the trivial keyword baseline on this set.")

    cache_hits = sum(1 for r in results if r.from_cache)
    print(f"\ncache: {cache_hits}/{len(results)} cases served from cache (no model call)")

    run_id = args.run_id or f"safety_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    config = {
        "suite": "safety", "policy_version": SAFETY_POLICY_VERSION,
        "utility_model": provider.utility_model, "sample": args.sample, "n_cases": len(cases),
    }
    artifact_path = save_run_artifact(run_id, config, results, metrics)
    print(f"\nrun artifact: {artifact_path}")

    if args.baseline:
        try:
            baseline_run = load_run_artifact(args.baseline)
        except FileNotFoundError:
            print(f"\n--baseline {args.baseline!r}: no stored run artifact found (eval/runs/{args.baseline}.json)", file=sys.stderr)
            return 1
        diff = diff_metrics(metrics, baseline_run)
        print(f"\n=== vs stored run {args.baseline!r} ===")
        print(
            f"recall_delta={diff['recall_delta']:+.2f}  precision_delta={diff['precision_delta']:+.2f}  "
            f"false_positive_rate_delta={diff['false_positive_rate_delta']:+.2f}"
        )
        if diff["recall_delta"] < -0.01:
            print(f"REGRESSION: recall dropped {diff['recall_delta']:.2f} vs {args.baseline!r}.")

    return 0


def write_batch(args: argparse.Namespace) -> int:
    cases = load_cases(split=args.split)
    if args.sample:
        cases = cases[: args.sample]
    model = args.model or "gpt-4o-mini"
    requests = build_stage2_batch_requests(cases, model)
    with open(args.out, "w") as f:
        for request in requests:
            f.write(json.dumps(request) + "\n")
    print(f"wrote {len(requests)} Batch API request lines to {args.out}")
    print("submit it yourself, e.g.:")
    print("  client.files.create(file=open(path, 'rb'), purpose='batch')")
    print("  client.batches.create(input_file_id=..., endpoint='/v1/chat/completions', completion_window='24h')")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kizuna")
    subparsers = parser.add_subparsers(dest="command", required=True)

    eval_parser = subparsers.add_parser("eval", help="run a labeled eval suite through the real pipeline")
    eval_parser.add_argument("--suite", required=True, choices=("safety", *STUB_SUITES))
    eval_parser.add_argument("--sample", type=int, default=None, help="run only the first N held-out cases")
    eval_parser.add_argument("--baseline", default=None, help="run_id of a stored run to diff against")
    eval_parser.add_argument("--run-id", default=None, help="override the generated run id")
    eval_parser.add_argument("--no-cache", action="store_true", help="ignore the per-case result cache")

    batch_parser = subparsers.add_parser(
        "write-batch", help="write an OpenAI Batch API request file for a Stage 2 sweep (not submitted)"
    )
    batch_parser.add_argument("--split", choices=("dev", "heldout"), default="heldout")
    batch_parser.add_argument("--sample", type=int, default=None)
    batch_parser.add_argument("--model", default=None)
    batch_parser.add_argument("--out", default="eval/runs/stage2_batch_requests.jsonl")

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "eval":
        if args.suite in STUB_SUITES:
            print(f"--suite {args.suite} is a scaffold for a later phase; nothing to run yet.")
            return 0
        return asyncio.run(run_safety_suite(args))

    if args.command == "write-batch":
        return write_batch(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
