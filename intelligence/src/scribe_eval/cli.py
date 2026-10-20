"""scribe-eval CLI.

    uv run --project intelligence scribe-eval validate benchmarks/datasets/golden-v1
    uv run --project intelligence scribe-eval assign-splits <dataset_dir> --seed N
    uv run --project intelligence scribe-eval score --rankings R.jsonl --dataset benchmarks/datasets/golden-v1 --split dev
    uv run --project intelligence scribe-eval run-profile --profile hybrid-rrf --split dev --database-url URL --out R.jsonl
    uv run --project intelligence scribe-eval compare <run_a_dir> <run_b_dir>
    uv run --project intelligence scribe-eval answer --rankings R.jsonl --split dev|validation [--generator real|fake]
    uv run --project intelligence scribe-eval judge-sheet --run <answer run dir> --out-dir <dir>
    uv run --project intelligence scribe-eval publish-run <run_dir> --database-url URL [--seeded|--mark-fake]
    uv run --project intelligence scribe-eval gate --database-url <test db> [--refresh-cache|--write-baseline]
    uv run --project intelligence scribe-eval judge-agreement --key <dir>/judge_key.json [--labels human_labels.csv]
"""

from __future__ import annotations

import argparse
import sys

from .commands import register_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scribe-eval")
    sub = parser.add_subparsers(dest="command", required=True)
    register_all(sub)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
