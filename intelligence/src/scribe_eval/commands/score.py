"""score subcommand."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..dataset import load_cases
from ..review.require import refusal_message, unreviewed_ids
from ..runs import DEFAULT_RUNS_DIR, RunRecord, config_hash, now_iso
from ..scoring import ScoreError, dataset_version, format_table, score_split, summary_to_json


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
        if args.milestone_report:
            split_cases = [c for c in load_cases(dataset_dir / "cases.jsonl") if c.split == args.split]
            unreviewed = unreviewed_ids(split_cases)
            if unreviewed:
                print(refusal_message(unreviewed, len(split_cases), f"score a milestone report on split '{args.split}'"),
                      file=sys.stderr)
                return 2
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


def register(sub: argparse._SubParsersAction) -> None:
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
