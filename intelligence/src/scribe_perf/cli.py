"""scribe-perf CLI.

    scribe-perf run --database-url postgresql://u:p@localhost:54329/perf_t32 --scales 1000,10000,100000 \
        --out benchmarks/perf/results.json [--drop]
    scribe-perf render benchmarks/perf/results.json
    scribe-perf drop --database-url postgresql://u:p@localhost:54329/perf_t32
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from .analysis import render_markdown


def _run(a: argparse.Namespace) -> int:
    from .pgdb import database_exists, disk_free_gb, drop_database, require_throwaway
    from .runner import run_scales

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
    from .pgdb import database_exists, drop_database

    drop_database(a.database_url)
    print(f"dropped; exists={database_exists(a.database_url)}")
    return 0


def _probe(a: argparse.Namespace) -> int:
    token = a.token or os.environ.get("SCRIBE_PERF_TOKEN")
    if not token:
        print("probe requires --token or SCRIBE_PERF_TOKEN", file=sys.stderr)
        return 2
    if a.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2
    questions = [q.strip() for q in a.question if q.strip()]
    if not questions:
        print("probe requires at least one --question", file=sys.stderr)
        return 2

    latencies: list[float] = []
    costs: list[float] = []
    estimated = False
    endpoint = a.base_url.rstrip("/") + "/api/py/retrieve"
    for i in range(a.count):
        payload = json.dumps({"query": questions[i % len(questions)]}).encode()
        request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=a.timeout) as response:
                body = json.loads(response.read())
        except Exception as exc:
            print(f"probe request {i + 1}/{a.count} failed ({type(exc).__name__})", file=sys.stderr)
            return 1
        if not isinstance(body, dict):
            print(f"probe request {i + 1}/{a.count} failed (invalid response)", file=sys.stderr)
            return 1
        latencies.append((time.perf_counter() - started) * 1000)
        cost = body.get("cost")
        usd = cost.get("usd") if isinstance(cost, dict) else None
        if isinstance(usd, (int, float)) and not isinstance(usd, bool):
            costs.append(float(usd))
            estimated = estimated or cost.get("estimated") is True

    from .stats import percentile

    print(f"queries: {len(latencies)}")
    print(f"latency p50: {percentile(latencies, 50):.3f} ms")
    print(f"latency p95: {percentile(latencies, 95):.3f} ms")
    if len(costs) == len(latencies):
        label = "estimated $/query" if estimated else "$/query"
        print(f"{label}: {sum(costs) / len(costs):.8f}")
    else:
        print("$/query: NOT MEASURED (retrieval cost missing)")
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
    probe = sub.add_parser("probe", help="measure HTTP retrieval latency and reported retrieval cost")
    probe.add_argument("--base-url", required=True)
    probe.add_argument("--count", type=int, default=10)
    probe.add_argument("--question", action="append", default=[])
    probe.add_argument("--token", help="opaque bearer token (or set SCRIBE_PERF_TOKEN)")
    probe.add_argument("--timeout", type=float, default=30.0)
    probe.set_defaults(fn=_probe)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
