"""Throwaway database lifecycle. Every destructive operation requires a perf/test-named database."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[3]
ALLOWED_PREFIXES = ("perf_", "t32_test_")
HNSW_INDEX = "chunk_embeddings_1536_hnsw"
HALF_TABLE = "chunk_embeddings_1536_half"
DISK_PATH = "/System/Volumes/Data"


def db_name(url: str) -> str:
    return urlsplit(url).path.lstrip("/")


def require_throwaway(url: str) -> str:
    name = db_name(url)
    if not name.startswith(ALLOWED_PREFIXES):
        raise ValueError(f"refusing to touch database {name!r}: perf databases must start with {ALLOWED_PREFIXES}")
    return name


def admin_url(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))


def with_database(url: str, name: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, "/" + name, p.query, p.fragment))


def database_exists(url: str) -> bool:
    with psycopg.connect(admin_url(url), autocommit=True) as c:
        return c.execute("select 1 from pg_database where datname = %s", (db_name(url),)).fetchone() is not None


def create_database(url: str) -> None:
    name = require_throwaway(url)
    with psycopg.connect(admin_url(url), autocommit=True) as c:
        if c.execute("select 1 from pg_database where datname = %s", (name,)).fetchone() is None:
            c.execute(f'create database "{name}"')


def drop_database(url: str) -> None:
    name = require_throwaway(url)
    with psycopg.connect(admin_url(url), autocommit=True) as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                  (name,))
        c.execute(f'drop database if exists "{name}"')


def apply_schema(url: str) -> None:
    """Replay the project schema (scripts/apply-migrations.sh: drizzle export + hand-written migrations)."""
    require_throwaway(url)
    script = REPO_ROOT / "scripts" / "apply-migrations.sh"
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
    r = subprocess.run(["bash", str(script), url], cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"apply-migrations.sh failed: {r.stderr[-800:]}")


def has_schema(url: str) -> bool:
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute("select to_regclass('public.chunk_embeddings_1536')").fetchone()[0] is not None


def ensure_schema(url: str) -> None:
    create_database(url)
    if not has_schema(url):
        apply_schema(url)


def reset_data(conn: psycopg.Connection) -> None:
    """Remove all workspace data and the perf-only objects (halfvec table). Keeps schema and seeded profiles."""
    if not conn.info.dbname.startswith(ALLOWED_PREFIXES):
        raise ValueError(f"refusing to reset database {conn.info.dbname!r}")
    conn.execute(f"drop table if exists {HALF_TABLE}")
    conn.execute("truncate workspaces cascade")


def disk_free_gb(path: str = DISK_PATH) -> float:
    return round(shutil.disk_usage(path).free / 2**30, 2)


def timed(conn: psycopg.Connection, sql: str) -> float:
    t0 = time.perf_counter()
    conn.execute(sql)
    return round(time.perf_counter() - t0, 3)
