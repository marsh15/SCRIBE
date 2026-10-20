"""validate and assign-splits subcommands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..splits import assign_splits_file
from ..validate import summary_table, validate_dataset


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
    n = assign_splits_file(Path(args.dataset_dir), args.seed, args.only_origin)
    print(f"assigned splits for {n} cases (seed={args.seed})")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    v = sub.add_parser("validate", help="validate a golden dataset against the corpus")
    v.add_argument("dataset_dir")
    v.add_argument("--corpus", default="fixtures/corpus")
    v.set_defaults(func=_validate)
    a = sub.add_parser("assign-splits", help="deterministically assign dev/validation/test splits")
    a.add_argument("dataset_dir")
    a.add_argument("--seed", type=int, required=True)
    a.add_argument("--only-origin", default=None, help="assign only cases with this origin; others keep their split")
    a.set_defaults(func=_assign)
