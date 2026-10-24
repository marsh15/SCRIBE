"""run-profile subcommand."""

from __future__ import annotations

import argparse


def _run_profile(args: argparse.Namespace) -> int:
    from ..run_profile import run

    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    rp = sub.add_parser("run-profile", help="run retrieval profile(s) on a dataset's ingested eval workspace, write rankings")
    rp.add_argument("--profile", default=None, help="one profile; --out is a rankings file")
    rp.add_argument("--profiles", default=None, help="comma-separated profiles; --out is then a directory (<profile>.jsonl)")
    rp.add_argument("--split", required=True, choices=["dev", "validation", "test"])
    rp.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    rp.add_argument("--dataset", default="benchmarks/datasets/golden-v1")
    rp.add_argument("--corpus", default="fixtures/corpus", help="corpus the dataset's workspace was ingested from (recorded)")
    rp.add_argument("--out", required=True, help="rankings JSONL to write (a directory with --profiles)")
    rp.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    rp.add_argument("--limit", type=int, default=0, help="first N cases only (smoke test)")
    rp.add_argument("--milestone-report", action="store_true", help="required to run the test split (INV-12, once per dataset)")
    rp.add_argument("--milestone-id", default=None, help="id of the milestone; the same id resumes an interrupted test-split run")
    rp.add_argument("--runs-dir", default="benchmarks/runs", help="where <dataset>/test-split.lock lives (git-ignored)")
    rp.add_argument("--spend-cap", type=float, default=None, help="USD cap for ledger total + estimate; can only lower $10")
    rp.add_argument("--ledger", default="benchmarks/spend-ledger.jsonl")
    rp.set_defaults(func=_run_profile)
