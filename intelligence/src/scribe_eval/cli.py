"""scribe-eval CLI.

    uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
    uv run --project intelligence scribe-eval assign-splits <dataset_dir> --seed N
    uv run --project intelligence scribe-eval score --rankings R.jsonl --dataset benchmarks/datasets/golden-v1 --split dev
    uv run --project intelligence scribe-eval compare <run_a_dir> <run_b_dir>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bootstrap import paired_bootstrap
from .metrics import METRIC_NAMES
from .runs import DEFAULT_RUNS_DIR, RunRecord, config_hash, load_run, now_iso
from .scoring import ScoreError, dataset_version, format_table, score_split, summary_to_json
from .splits import assign_splits_file
from .validate import summary_table, validate_dataset


def _validate(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset_dir)
    corpus_dir = Path(args.corpus)
    if not corpus_dir.is_dir():
        print(f"ERROR: corpus directory not found: {corpus_dir}", file=sys.stderr)
        return 2
    cases, errors = validate_dataset(dataset_dir, corpus_dir)
    if cases:
        print(summary_table(cases))
    if errors:
        print(f"\nFAILED: {len(errors)} problem(s)", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("\nOK")
    return 0


def _assign(args: argparse.Namespace) -> int:
    path = Path(args.dataset_dir) / "cases.jsonl"
    if not path.is_file():
        print(f"ERROR: missing {path}", file=sys.stderr)
        return 2
    n = assign_splits_file(Path(args.dataset_dir), args.seed)
    print(f"assigned splits for {n} cases (seed={args.seed})")
    return 0


def _kv(items: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for it in items or []:
        k, sep, v = it.partition("=")
        if not sep:
            raise ValueError(f"expected KEY=VALUE, got {it!r}")
        out[k] = v
    return out


def _score(args: argparse.Namespace) -> int:
    if args.split == "test" and not args.milestone_report:
        print(
            "ERROR: --split test is refused (INV-12): the test split is used only for milestone "
            "reports, never for tuning. Pass --milestone-report if this is a milestone report.",
            file=sys.stderr,
        )
        return 2
    dataset_dir = Path(args.dataset)
    started = now_iso()
    try:
        models, prompts = _kv(args.model), _kv(args.prompt)
        _, results, summary = score_split(dataset_dir, args.split, Path(args.rankings))
    except (ScoreError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_table(summary))
    run = RunRecord(
        dataset_version=dataset_version(dataset_dir), split=args.split, profile_name=args.profile,
        config_hash=config_hash(args.profile), model_ids=models, prompt_versions=prompts,
        started_at=started, summary=summary_to_json(summary),
    )
    run_dir = run.write(args.out, results)
    print(f"\nrun written: {run_dir}")
    return 0


def _compare(args: argparse.Namespace) -> int:
    try:
        run_a, rows_a = load_run(args.run_a)
        run_b, rows_b = load_run(args.run_b)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"A: {run_a['profile_name']} ({run_a['run_id']})\nB: {run_b['profile_name']} ({run_b['run_id']})")
    print(f"{'metric':<12}{'n':>4}{'mean(B-A)':>11}   95% interval")
    for name in METRIC_NAMES:
        a = {r["case_id"]: r["metrics"][name] for r in rows_a if r.get("metrics")}
        b = {r["case_id"]: r["metrics"][name] for r in rows_b if r.get("metrics")}
        res = paired_bootstrap(a, b)
        flag = " *" if res.significant else ""
        print(f"{name:<12}{res.n:>4}{res.mean_diff:>+11.4f}   [{res.lo:+.4f}, {res.hi:+.4f}]{flag}")
    print("* interval excludes 0")
    return 0


def _ingest_corpus(args: argparse.Namespace) -> int:
    from .ingest_corpus import run

    return run(args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate", help="validate a golden dataset against the corpus")
    v.add_argument("dataset_dir")
    v.add_argument("--corpus", default="fixtures/corpus")
    v.set_defaults(func=_validate)
    a = sub.add_parser("assign-splits", help="deterministically assign dev/validation/test splits")
    a.add_argument("dataset_dir")
    a.add_argument("--seed", type=int, required=True)
    a.set_defaults(func=_assign)
    sc = sub.add_parser("score", help="score a rankings JSONL file on a dataset split")
    sc.add_argument("--rankings", required=True)
    sc.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    sc.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    sc.add_argument("--profile", default="unnamed")
    sc.add_argument("--out", default=str(DEFAULT_RUNS_DIR))
    sc.add_argument("--milestone-report", action="store_true", help="required to score the test split (INV-12)")
    sc.add_argument("--model", action="append", help="KEY=MODEL_ID recorded in the run (repeatable)")
    sc.add_argument("--prompt", action="append", help="KEY=VERSION recorded in the run (repeatable)")
    sc.set_defaults(func=_score)
    cp = sub.add_parser("compare", help="paired bootstrap comparison of two run dirs")
    cp.add_argument("run_a")
    cp.add_argument("run_b")
    cp.set_defaults(func=_compare)
    ic = sub.add_parser("ingest-corpus", help="index the corpus into the eval workspace (T12)")
    ic.add_argument("--corpus", default="fixtures/corpus")
    ic.add_argument("--profile", default="structural-v1", choices=["structural-v1"])
    ic.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    ic.add_argument("--embedder", default="gemini", choices=["fake", "gemini"])
    ic.add_argument("--reset", action="store_true", help="wipe the eval workspace's rows first")
    ic.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    ic.set_defaults(func=_ingest_corpus)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
