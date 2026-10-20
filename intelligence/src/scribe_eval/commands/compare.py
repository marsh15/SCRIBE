"""compare subcommand."""

from __future__ import annotations

import argparse
import sys

from ..bootstrap import paired_bootstrap
from ..metrics import METRIC_NAMES
from ..runs import load_run


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


def register(sub: argparse._SubParsersAction) -> None:
    cp = sub.add_parser("compare", help="paired bootstrap comparison of two run dirs")
    cp.add_argument("run_a")
    cp.add_argument("run_b")
    cp.set_defaults(func=_compare)
