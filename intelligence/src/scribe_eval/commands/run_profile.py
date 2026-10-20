"""run-profile subcommand."""

from __future__ import annotations

import argparse


def _run_profile(args: argparse.Namespace) -> int:
    from ..run_profile import run

    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    rp = sub.add_parser("run-profile", help="run a retrieval profile on the ingested eval workspace, write rankings")
    rp.add_argument("--profile", required=True)
    rp.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    rp.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    rp.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    rp.add_argument("--out", required=True, help="rankings JSONL to write")
    rp.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    rp.add_argument("--limit", type=int, default=0, help="first N cases only (smoke test)")
    rp.set_defaults(func=_run_profile)
