"""ingest-corpus subcommand."""

from __future__ import annotations

import argparse


def _ingest_corpus(args: argparse.Namespace) -> int:
    from ..ingest_corpus import run

    return run(args)


def register(sub: argparse._SubParsersAction) -> None:
    ic = sub.add_parser("ingest-corpus", help="index the corpus into the eval workspace (T12)")
    ic.add_argument("--corpus", default="fixtures/corpus")
    ic.add_argument("--dataset", default="benchmarks/datasets/golden-v1",
                    help="dataset whose eval workspace is filled (owner id eval-<dataset dir name>)")
    ic.add_argument("--manifest", default=None,
                    help="corpus manifest.json: ingest each document's ingest_file under the name <id>.pdf")
    ic.add_argument("--spend-cap", type=float, default=None, help="USD cap for ledger total + estimate; can only lower $10")
    ic.add_argument("--ledger", default="benchmarks/spend-ledger.jsonl")
    ic.add_argument("--profile", default="structural-v1", choices=["structural-v1"])
    ic.add_argument("--database-url", default=None, help="required; the environment is never consulted")
    ic.add_argument("--embedder", default="gemini", choices=["fake", "gemini"])
    ic.add_argument("--reset", action="store_true", help="wipe the eval workspace's rows first")
    ic.add_argument("--cache-path", default="benchmarks/cache/py-embeddings.sqlite")
    ic.set_defaults(func=_ingest_corpus)
