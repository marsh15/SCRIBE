"""review subcommand: a local page for a person to check every evaluation item against its source (task A1)."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

DEFAULT_PORT = 8765


def _review(args: argparse.Namespace) -> int:
    # Imported here so `scribe-eval` itself never pulls in FastAPI/uvicorn unless this command runs.
    from ..review.app import build_order, create_app
    from ..review.server import serve

    items = Path(args.items)
    if not items.is_file():
        print(f"ERROR: items file not found: {items}", file=sys.stderr)
        return 2
    if not Path(args.corpus).is_dir():
        print(f"ERROR: corpus directory not found: {args.corpus}", file=sys.stderr)
        return 2
    try:
        order = build_order(items, args.order)
        reviewer = args.reviewer or getpass.getuser()
        app = create_app(items, args.kind, args.corpus, reviewer, order)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    state = app.state.store.state()
    print(f"reviewing {state['total']} {args.kind} item(s) from {items} as {reviewer!r}: "
          f"{state['reviewed']} reviewed, {state['remaining']} remaining", flush=True)
    print(f"open http://127.0.0.1:{args.port}/   (keys: a accept, e edit, d drop, n next; Ctrl+C to stop)", flush=True)
    try:
        serve(app, port=args.port)
    except OSError as exc:  # port in use
        print(f"ERROR: cannot serve on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    except SystemExit as exc:  # uvicorn exits (instead of raising) when the port is taken
        print(f"ERROR: the server did not start on 127.0.0.1:{args.port} (is the port in use?)", file=sys.stderr)
        return 2 if exc.code else 0
    except KeyboardInterrupt:
        pass
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    r = sub.add_parser("review", help="local page to review evaluation items next to their source pages (A1)")
    r.add_argument("--items", required=True, help="items JSONL (e.g. benchmarks/datasets/golden-v1/cases.jsonl)")
    r.add_argument("--kind", choices=["case", "field", "change"], default="case")
    r.add_argument("--corpus", default="fixtures/corpus")
    r.add_argument("--port", type=int, default=DEFAULT_PORT, help="port on 127.0.0.1 (the tool never binds another host)")
    r.add_argument("--reviewer", default=None, help="name recorded as reviewed_by / dropped_by (default: OS user)")
    r.add_argument("--order", default=None, help="file of item ids to review first (default for golden-v1: "
                   "REVIEW_FIRST.md then UNREVIEWED_T28.md next to the items file)")
    r.set_defaults(func=_review)
