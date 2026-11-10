"""Seed the retrieval profiles that no SQL migration seeds (the v2, flat and context-strategy profiles).

Migration drizzle/0012 seeds only profiles A-E. Every other profile in
scribe_intel.retrieval.profiles.ALL_PROFILES is written by profiles.upsert_profiles, which nothing
outside the tests called. On a migrated database those profiles were missing, so retrieval traces for
them got profile_id NULL.

When to run: once after migrations 0008..0020 are applied to a database, as the OWNER/migration role
(DATABASE_URL_OWNER). It is idempotent: a rerun inserts nothing and only verifies the stored rows. The
runtime role (scribe_app) has SELECT only on retrieval_profiles (drizzle/0013), so it cannot run this.

    python -m scribe_intel.retrieval.seed_cli --dry-run   # report only; exit 1 if a stored row differs
    python -m scribe_intel.retrieval.seed_cli            # insert the missing profiles and commit

Exit codes: 0 ok; 1 a stored profile differs from code (nothing written), or a database error;
2 bad usage (no database URL, or a pooled host without --allow-pooler).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

import psycopg
import psycopg.rows

from . import profiles as P

URL_ENV_VARS = ("DATABASE_URL_OWNER", "DATABASE_URL", "NEON_DATABASE_URL")


def _connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=False, row_factory=psycopg.rows.dict_row)


def _resolve_url(arg: str | None) -> str | None:
    if arg:
        return arg
    for var in URL_ENV_VARS:
        if os.environ.get(var):
            return os.environ[var]
    return None


def _stored(conn: Any) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT name, config, config_hash FROM retrieval_profiles WHERE name = ANY(%s)",
        ([p.name for p in P.ALL_PROFILES],),
    ).fetchall()
    return {r["name"]: r for r in rows}


def _status_of(row: dict[str, Any], p: P.RetrievalProfile) -> str:
    config = row["config"]
    if isinstance(config, str):
        config = json.loads(config)
    if config == p.config_json and row["config_hash"] == p.config_hash and P.hash_config(config) == p.config_hash:
        return "present"
    return "differs"


def _dry_run(conn: Any) -> int:
    stored = _stored(conn)
    counts = {"missing": 0, "present": 0, "differs": 0}
    for p in P.ALL_PROFILES:
        status = "missing" if p.name not in stored else _status_of(stored[p.name], p)
        counts[status] += 1
        print(f"{p.name}: {status}")
    print(f"dry run (nothing written): {counts['missing']} missing, {counts['present']} present, "
          f"{counts['differs']} differ from code")
    return 1 if counts["differs"] else 0


def _seed(conn: Any) -> int:
    before = set(_stored(conn))
    try:
        P.upsert_profiles(conn)
    except P.ProfileImmutableError as e:
        conn.rollback()
        print(str(e), file=sys.stderr)
        return 1
    conn.commit()
    inserted = 0
    for p in P.ALL_PROFILES:
        if p.name in before:
            print(f"{p.name}: already present")
        else:
            inserted += 1
            print(f"{p.name}: inserted")
    print(f"done: {inserted} inserted, {len(P.ALL_PROFILES) - inserted} already present")
    return 0


def _run(url: str, *, dry_run: bool) -> int:
    conn = _connect(url)
    try:
        if dry_run:
            conn.read_only = True
            return _dry_run(conn)
        return _seed(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scribe_intel.retrieval.seed_cli",
                                 description=__doc__.split("\n\n")[0])
    ap.add_argument("--database-url", default=None,
                    help=f"owner/migration DSN (default: first set of {', '.join(URL_ENV_VARS)})")
    ap.add_argument("--dry-run", action="store_true",
                    help="report missing, present and differing profiles; write nothing")
    ap.add_argument("--allow-pooler", action="store_true",
                    help="allow a host containing '-pooler' (pooled connections do not suit migrations)")
    args = ap.parse_args(argv)

    url = _resolve_url(args.database_url)
    if not url:
        print(f"no database URL: pass --database-url or set {' / '.join(URL_ENV_VARS)}", file=sys.stderr)
        return 2
    host = urlsplit(url).hostname or ""
    if "-pooler" in host and not args.allow_pooler:
        print(f"refusing pooled host {host!r}: use a direct connection, or pass --allow-pooler",
              file=sys.stderr)
        return 2
    try:
        return _run(url, dry_run=args.dry_run)
    except psycopg.Error as e:
        print(f"database error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
