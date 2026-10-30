"""Journal registration must be repeatable and refuse drift atomically; no production connections."""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def isolated_database():
    parsed = urlsplit(DATABASE_URL_TEST)
    # Even an accidentally supplied remote test URL must never create a database there.
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1", "db"} or "test" not in parsed.path.lower():
        pytest.skip("journal registration tests require a local test database")
    name = f"scribe_journal_{uuid.uuid4().hex[:12]}_test"
    target = urlunsplit(parsed._replace(path=f"/{name}"))
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(name)))
        try:
            yield target
        finally:
            admin.execute(sql.SQL("drop database {} with (force)").format(sql.Identifier(name)))


def register(url: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(ROOT / "scripts/neon-rehearsal/apply.sh"), url, "--register-journal"],
        cwd=ROOT, env={**os.environ, "CONFIRM_HOST": urlsplit(url).hostname, "PROD_HOST": ""},
        capture_output=True, text=True, timeout=30,
    )


def test_register_is_idempotent_and_does_not_change_existing_rows(isolated_database):
    assert register(isolated_database).returncode == 0
    with psycopg.connect(isolated_database, autocommit=True) as conn:
        before = conn.execute("select hash, created_at from drizzle.__drizzle_migrations order by created_at").fetchall()
        assert len(before) == 13
        assert register(isolated_database).returncode == 0
        assert conn.execute("select hash, created_at from drizzle.__drizzle_migrations order by created_at").fetchall() == before


@pytest.mark.parametrize("drift", ["hash", "timestamp", "duplicate", "future", "null"])
def test_register_refuses_conflicting_metadata_without_partial_registration(isolated_database, drift):
    assert register(isolated_database).returncode == 0
    with psycopg.connect(isolated_database, autocommit=True) as conn:
        # Leave an early migration missing: a later mismatch must roll back even that earlier insertion.
        conn.execute("delete from drizzle.__drizzle_migrations where created_at = (select min(created_at) from drizzle.__drizzle_migrations)")
        if drift == "hash":
            conn.execute("update drizzle.__drizzle_migrations set hash = 'wrong' where created_at = (select max(created_at) from drizzle.__drizzle_migrations)")
        elif drift == "timestamp":
            conn.execute("update drizzle.__drizzle_migrations set created_at = created_at + 1 where created_at = (select max(created_at) from drizzle.__drizzle_migrations)")
        elif drift == "duplicate":
            conn.execute("insert into drizzle.__drizzle_migrations(hash, created_at) select hash, created_at from drizzle.__drizzle_migrations order by created_at desc limit 1")
        else:
            timestamp = 9999999999999 if drift == "future" else None
            conn.execute("insert into drizzle.__drizzle_migrations(hash, created_at) values ('unknown', %s)", (timestamp,))
        before = conn.execute("select hash, created_at from drizzle.__drizzle_migrations order by created_at").fetchall()
        result = register(isolated_database)
        assert result.returncode != 0
        assert "migration journal mismatch" in result.stderr
        assert conn.execute("select hash, created_at from drizzle.__drizzle_migrations order by created_at").fetchall() == before
