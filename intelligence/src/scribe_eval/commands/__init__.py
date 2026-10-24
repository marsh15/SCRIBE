"""Subcommand registry for scribe-eval.

Each module in ``MODULES`` exposes ``register(sub)``, which adds its parser(s) to the
subparsers action and wires ``set_defaults(func=...)``. To add a subcommand: create a
module here and append its name to ``MODULES`` (the only shared line). ``MODULES`` order
is the ``--help`` order.
"""

from __future__ import annotations

import argparse
from importlib import import_module

MODULES: tuple[str, ...] = (
    "validate",
    "score",
    "answer",
    "compare",
    "publish",
    "gate",
    "ingest",
    "run_profile",
    "review",
    "fetch_corpus",
    "report",
    "parse_compare",
)


def register_all(sub: argparse._SubParsersAction) -> None:
    for name in MODULES:
        import_module(f"{__name__}.{name}").register(sub)
