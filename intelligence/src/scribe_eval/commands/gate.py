"""gate subcommand."""

from __future__ import annotations

import argparse

from ..gate import DEFAULT_BASELINE, DEFAULT_CACHE, DEFAULT_CORPUS, DEFAULT_DATASET, DEFAULT_PROFILE, DEFAULT_SPLIT


def _gate(args: argparse.Namespace) -> int:
    from ..gate import run_gate

    return run_gate(args)


def register(sub: argparse._SubParsersAction) -> None:
    gt = sub.add_parser("gate", help="retrieval regression gate from cached provider responses (T31)")
    gt.add_argument("--database-url", default=None, help="throwaway database whose name contains 'test'; env never consulted")
    gt.add_argument("--dataset", default=DEFAULT_DATASET)
    gt.add_argument("--corpus", default=DEFAULT_CORPUS)
    gt.add_argument("--split", default=DEFAULT_SPLIT, choices=["dev", "validation", "test"])
    gt.add_argument("--profile", default=DEFAULT_PROFILE)
    gt.add_argument("--baseline", default=DEFAULT_BASELINE)
    gt.add_argument("--cache", default=DEFAULT_CACHE, help="committed jsonl.gz of cached embedding + rerank responses")
    gt.add_argument("--tau", type=float, default=None, help="abstention threshold (default: the profile's gate tau)")
    gt.add_argument("--out", default=None, help="also write gate-result.json here")
    gt.add_argument("--refresh-cache", action="store_true", help="LOCAL ONLY: call the providers and rewrite the cache")
    gt.add_argument("--write-baseline", action="store_true", help="replay the cache and write the baseline file")
    gt.add_argument("--status", action="store_true", help="print whether a real baseline exists (and set the GitHub 'armed' output); exit 0")
    gt.add_argument("--seeded", action="store_true", help="label the result as a seeded fixture (never armed)")
    gt.add_argument("--accept-seeded-baseline", action="store_true", help="tests only: treat a seeded baseline as armed")
    gt.set_defaults(func=_gate)
