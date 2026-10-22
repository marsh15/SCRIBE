"""Render an honest markdown report from persisted eval run directories."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..report import ReportError, build_report


def _report(args: argparse.Namespace) -> int:
    try:
        report = build_report(args.run_dirs, args.dataset)
    except (ReportError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report, end="")
    print(f"report written: {out}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser("report", help="aggregate persisted runs into dataset-separated markdown tables")
    parser.add_argument("run_dirs", nargs="+", help="one or more run directories")
    parser.add_argument("--dataset", action="append", required=True, help="dataset directory; repeat for multiple datasets")
    parser.add_argument("--out", required=True, help="markdown output path")
    parser.set_defaults(func=_report)
