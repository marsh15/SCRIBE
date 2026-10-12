"""scribe-perf CLI.

    scribe-perf run --database-url postgresql://u:p@localhost:54329/perf_t32 --scales 1000,10000,100000 \
        --out benchmarks/perf/results.json [--drop]
    scribe-perf render benchmarks/perf/results.json
    scribe-perf drop --database-url postgresql://u:p@localhost:54329/perf_t32
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import render_markdown
from .pgdb import database_exists, disk_free_gb, drop_database, require_throwaway
from .runner import run_scales


def _run(a: argparse.Namespace) -> int:
    require_throwaway(a.database_url)
    scales = [int(s) for s in a.scales.split(",") if s.strip()]
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731
    log(f"free disk before: {disk_free_gb()} GB")
    try:
        doc = run_scales(a.database_url, scales, seed=a.seed, n_queries=a.queries, drop_each=a.drop_each_scale, log=log)
    finally:
        if a.drop:
            drop_database(a.database_url)
            log(f"database dropped: {not database_exists(a.database_url)}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    log(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


def _render(a: argparse.Namespace) -> int:
    print(render_markdown(json.loads(Path(a.json).read_text())))
    return 0


def _drop(a: argparse.Namespace) -> int:
    drop_database(a.database_url)
    print(f"dropped; exists={database_exists(a.database_url)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scribe-perf")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--database-url", required=True, help="throwaway DB (name must start with perf_); created if missing")
    r.add_argument("--scales", default="1000,10000,100000")
    r.add_argument("--seed", type=int, default=32)
    r.add_argument("--queries", type=int, default=100)
    r.add_argument("--out", default="benchmarks/perf/results.json")
    r.add_argument("--drop", action="store_true", help="drop the database when finished")
    r.add_argument("--drop-each-scale", action="store_true", help="drop the database after every scale step")
    r.set_defaults(fn=_run)
    m = sub.add_parser("render")
    m.add_argument("json")
    m.set_defaults(fn=_render)
    d = sub.add_parser("drop")
    d.add_argument("--database-url", required=True)
    d.set_defaults(fn=_drop)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
