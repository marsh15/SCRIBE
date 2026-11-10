"""Tests for scribe_intel.retrieval.seed_cli.

The DB-free tests monkeypatch the connect function, so no network is used. The DB tests run the CLI
against DATABASE_URL_TEST, which COMMITS. Each DB test therefore removes, in a finally block, exactly
the profile rows it created (names that were absent before the test). The migration-seeded A-E rows
are never deleted or changed.
"""

import psycopg
import pytest

from conftest import DATABASE_URL_TEST, needs_db
from scribe_intel.retrieval import profiles as P
from scribe_intel.retrieval import seed_cli

NAMES = [p.name for p in P.ALL_PROFILES]
URL_VARS = ("DATABASE_URL_OWNER", "DATABASE_URL", "NEON_DATABASE_URL")
POOLED_URL = "postgresql://owner:secret@ep-cool-x-pooler.us-east-2.aws.neon.tech/scribe?sslmode=require"
DIRECT_URL = "postgresql://owner:secret@ep-cool-x.us-east-2.aws.neon.tech/scribe?sslmode=require"


class _Reached(Exception):
    """Raised by a fake connect to prove the CLI got past the pooler and URL checks."""


def _fake_connect_that_records(calls):
    def fake(url):
        calls.append(url)
        raise _Reached
    return fake


def _must_not_connect(url):
    raise AssertionError(f"CLI must not connect here (got {url!r})")


def _query(sql, params=()):
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        return c.execute(sql, params).fetchall()


def _present_names():
    return {r[0] for r in _query("select name from retrieval_profiles where name = any(%s)", (NAMES,))}


def _row_count():
    return _query("select count(*) from retrieval_profiles")[0][0]


def _remove_created(names):
    """Delete only the rows a test created. Their retrieval_traces go first (FK, no cascade)."""
    if not names:
        return
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    try:
        c.execute(
            "DELETE FROM retrieval_traces WHERE profile_id IN "
            "(SELECT id FROM retrieval_profiles WHERE name = ANY(%s))",
            (list(names),),
        )
        c.execute("DELETE FROM retrieval_profiles WHERE name = ANY(%s)", (list(names),))
        c.commit()
    finally:
        c.close()


def _statuses(out):
    """Map profile name -> status from the CLI's `<name>: <status>` lines."""
    parsed = (line.partition(": ") for line in out.splitlines())
    return {name: status for name, _, status in parsed if name in NAMES}


# ---------------------------------------------------------------- DB-free

def test_missing_database_url_exits_2(monkeypatch, capsys):
    for var in URL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(seed_cli, "_connect", _must_not_connect)

    assert seed_cli.main([]) == 2
    assert "DATABASE_URL_OWNER" in capsys.readouterr().err


def test_owner_url_env_wins_over_runtime_url(monkeypatch):
    calls = []
    monkeypatch.setattr(seed_cli, "_connect", _fake_connect_that_records(calls))
    for var in URL_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DATABASE_URL", DIRECT_URL.replace("owner", "app"))
    monkeypatch.setenv("DATABASE_URL_OWNER", DIRECT_URL)

    with pytest.raises(_Reached):
        seed_cli.main([])
    assert calls == [DIRECT_URL]


def test_pooled_host_refused_without_allow_pooler(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(seed_cli, "_connect", _fake_connect_that_records(calls))

    assert seed_cli.main(["--database-url", POOLED_URL]) == 2
    assert calls == []
    assert "--allow-pooler" in capsys.readouterr().err


def test_pooled_host_allowed_with_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(seed_cli, "_connect", _fake_connect_that_records(calls))

    with pytest.raises(_Reached):
        seed_cli.main(["--database-url", POOLED_URL, "--allow-pooler"])
    assert calls == [POOLED_URL]


def test_direct_host_is_not_refused(monkeypatch):
    calls = []
    monkeypatch.setattr(seed_cli, "_connect", _fake_connect_that_records(calls))

    with pytest.raises(_Reached):
        seed_cli.main(["--database-url", DIRECT_URL])
    assert calls == [DIRECT_URL]


# ---------------------------------------------------------------- DB

@needs_db
def test_cli_seeds_every_profile_and_is_idempotent(capsys):
    absent = [n for n in NAMES if n not in _present_names()]
    try:
        assert seed_cli.main(["--database-url", DATABASE_URL_TEST]) == 0
        first = capsys.readouterr().out
        assert _present_names() == set(NAMES)
        for p in P.ALL_PROFILES:
            (stored_hash,) = _query("SELECT config_hash FROM retrieval_profiles WHERE name = %s", (p.name,))[0]
            assert stored_hash == p.config_hash
        assert _statuses(first) == {n: ("inserted" if n in absent else "already present") for n in NAMES}
        count_after_first = _row_count()

        assert seed_cli.main(["--database-url", DATABASE_URL_TEST]) == 0
        second = capsys.readouterr().out
        assert _statuses(second) == {n: "already present" for n in NAMES}
        assert _row_count() == count_after_first
    finally:
        _remove_created(absent)


@needs_db
def test_dry_run_reports_state_and_writes_nothing(capsys):
    present_before = _present_names()
    count_before = _row_count()
    rows_before = _query("SELECT id::text, name FROM retrieval_profiles ORDER BY name")

    assert seed_cli.main(["--database-url", DATABASE_URL_TEST, "--dry-run"]) == 0
    out = capsys.readouterr().out

    assert _statuses(out) == {n: ("present" if n in present_before else "missing") for n in NAMES}
    assert _row_count() == count_before
    assert _query("SELECT id::text, name FROM retrieval_profiles ORDER BY name") == rows_before


@needs_db
def test_immutability_error_prints_message_rolls_back_and_exits_1(monkeypatch, capsys):
    message = "stored retrieval profile 'hybrid-rrf-v2' differs from code (test)"

    def raise_immutable(conn):
        raise P.ProfileImmutableError(message)

    monkeypatch.setattr(P, "upsert_profiles", raise_immutable)
    present_before = _present_names()
    count_before = _row_count()

    assert seed_cli.main(["--database-url", DATABASE_URL_TEST]) == 1
    assert message in capsys.readouterr().err
    assert _present_names() == present_before
    assert _row_count() == count_before
