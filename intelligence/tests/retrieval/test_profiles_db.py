"""DB-backed tests for retrieval profile seeding (T14 and C8).

Every test runs inside a transaction that is ALWAYS rolled back, so the shared test DB keeps
the five rows seeded by drizzle/0012_seed_retrieval_profiles.sql and verifies code-seeded rows.

The migration/code consistency test (test_migration_seed_matches_code) needs no database and is
deliberately left unmarked so it always runs.
"""

import json
import re
from pathlib import Path

import psycopg
import pytest
from psycopg.errors import RestrictViolation

from conftest import DATABASE_URL_TEST, needs_db
from scribe_intel.retrieval import profiles as P

MIGRATION = Path(__file__).resolve().parents[3] / "drizzle" / "0012_seed_retrieval_profiles.sql"
SEED_NAMES = [p.name for p in P.ALL_PROFILES]
SEED_IDS = P.SEED_IDS
ROW_RE = re.compile(
    r"\('([0-9a-f-]{36})',\s*'([^']+)',\s*'(\{[^']*\})',\s*'([0-9a-f]{64})'\)"
)


@pytest.fixture
def conn():
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _stored_rows(conn):
    cur = conn.execute(
        "SELECT id::text, name, config, config_hash FROM retrieval_profiles WHERE name = ANY(%s)",
        (SEED_NAMES,),
    )
    return {name: (rid, config, h) for rid, name, config, h in cur.fetchall()}


def _delete_profiles(conn, names):
    """Delete profile rows, first removing retrieval_traces rows that reference them.

    Other test modules commit traces that point at the seeded profiles (FK without cascade). Those
    traces are deleted here too, but only inside this test's transaction, which the `conn` fixture
    always rolls back, so the shared DB is left exactly as it was."""
    conn.execute(
        "DELETE FROM retrieval_traces WHERE profile_id IN "
        "(SELECT id FROM retrieval_profiles WHERE name = ANY(%s))",
        (list(names),),
    )
    conn.execute("DELETE FROM retrieval_profiles WHERE name = ANY(%s)", (list(names),))


def _replace_row(conn, name, config, config_hash):
    """Delete the seeded row and re-insert it with the given config/hash (same name and id)."""
    _delete_profiles(conn, [name])
    conn.execute(
        "INSERT INTO retrieval_profiles (id, name, config, config_hash) "
        "VALUES (%s::uuid, %s, %s::jsonb, %s)",
        (SEED_IDS[name], name, json.dumps(config), config_hash),
    )


@needs_db
def test_upsert_against_real_db_is_idempotent_and_matches_code(conn):
    P.upsert_profiles(conn)
    P.upsert_profiles(conn)  # second call must be a no-op that still verifies

    rows = _stored_rows(conn)
    assert set(rows) == set(SEED_NAMES)
    for p in P.ALL_PROFILES:
        rid, config, config_hash = rows[p.name]
        assert rid == SEED_IDS[p.name]
        assert config == p.config_json
        assert config_hash == p.config_hash


@needs_db
def test_upsert_on_empty_table_inserts_all_profiles(conn):
    _delete_profiles(conn, SEED_NAMES)
    assert _stored_rows(conn) == {}

    P.upsert_profiles(conn)

    rows = _stored_rows(conn)
    assert len(rows) == len(P.ALL_PROFILES)
    for p in P.ALL_PROFILES:
        rid, config, config_hash = rows[p.name]
        assert rid == SEED_IDS[p.name]
        assert config == p.config_json
        assert config_hash == p.config_hash


@needs_db
def test_drift_config_with_original_hash_is_rejected(conn):
    p = P.BY_NAME["hybrid-rrf"]
    drifted = {**p.config_json, "rrf_k": 61}
    _replace_row(conn, p.name, drifted, p.config_hash)

    with pytest.raises(P.ProfileImmutableError):
        P.upsert_profiles(conn)


@needs_db
def test_matching_config_with_wrong_stored_hash_is_rejected(conn):
    p = P.BY_NAME["hybrid-rrf"]
    _replace_row(conn, p.name, p.config_json, "0" * 64)

    with pytest.raises(P.ProfileImmutableError):
        P.upsert_profiles(conn)


@needs_db
def test_immutability_trigger_rejects_update(conn):
    with pytest.raises(RestrictViolation, match="immutable"):
        conn.execute(
            "UPDATE retrieval_profiles SET config_hash = 'x' WHERE name = 'hybrid-rrf'"
        )


@needs_db
def test_stored_sql_hash_differs_but_python_hash_matches_stored_config(conn):
    P.upsert_profiles(conn)
    stored_config, stored_hash = conn.execute(
        "SELECT config, config_hash FROM retrieval_profiles WHERE name = 'hybrid-rrf'"
    ).fetchone()

    sql_hash = conn.execute(
        "SELECT encode(sha256(convert_to(config::text,'UTF8')),'hex') "
        "FROM retrieval_profiles WHERE name = 'hybrid-rrf'"
    ).fetchone()[0]

    # Documents T14-notes decision 2: stored hash is the Python canonical-JSON hash,
    # not the sha256(jsonb::text) formula.
    assert sql_hash != stored_hash
    assert P.hash_config(stored_config) == stored_hash


def test_migration_seed_matches_code():
    sql = MIGRATION.read_text(encoding="utf-8")
    parsed = [tuple(m) for m in ROW_RE.findall(sql)]
    expected = [params for _, params in P.seed_sql()]
    assert len(parsed) == 5
    assert parsed == expected[:5]  # migration 0012 seeds A-E only; the v2/flat profiles go through upsert_profiles
