"""publish-run subcommand."""

from __future__ import annotations

import argparse


def _publish_run(args: argparse.Namespace) -> int:
    from ..publish import run

    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    pr = sub.add_parser("publish-run", help="load a run dir into eval_runs/eval_results for the /eval dashboard (T30)")
    pr.add_argument("run_dir")
    pr.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    pr.add_argument("--dataset", default="benchmarks/datasets/golden-v1", help="gold cases, for failure categories")
    pr.add_argument("--seeded", action="store_true", help="label as SEEDED TEST DATA (never a measurement)")
    pr.add_argument("--mark-fake", action="store_true", help="force the FAKE - NOT A MEASUREMENT stamp")
    pr.add_argument("--parse-lost", action="append", default=[], help="gold document known to be lost by the parser (repeatable)")
    pr.set_defaults(func=_publish_run)
