"""T33: the demo seed's safety guard, dry run and (with a test DB) idempotent seeding with the FAKE embedder."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "demo-seed" / "demo_seed.py"
spec = importlib.util.spec_from_file_location("demo_seed", SCRIPT)
demo_seed = importlib.util.module_from_spec(spec)
sys.modules["demo_seed"] = demo_seed
spec.loader.exec_module(demo_seed)


@pytest.mark.parametrize("url", [
    "postgresql://scribe:scribe@localhost:54329/scribe",
    "postgresql://scribe:scribe@127.0.0.1:54329/t13_test",
    "postgresql://u:p@ep-cool-123.eu-central-1.aws.neon.tech/demo_branch",
    "postgresql://u:p@db.example.com/scribe_staging",
])
def test_non_production_targets_pass(url):
    safe, _ = demo_seed.assess_target(url)
    assert safe


@pytest.mark.parametrize("url", [
    "postgresql://u:p@ep-cool-123.eu-central-1.aws.neon.tech/neondb",
    "postgresql://u:p@ep-cool-123.eu-central-1.aws.neon.tech/scribe",      # remote, no demo/dev hint
    "postgresql://scribe:scribe@localhost:54329/scribe_prod_copy",
    "postgresql://scribe:scribe@localhost:54329/production",
    "postgresql://scribe:scribe@prod-db.internal/demo",                     # host looks like production
    "postgresql://scribe:scribe@localhost:54329/",                          # no database name
    "postgresql://u:p@db.example.com/main",
])
def test_production_looking_targets_are_refused_without_i_know(url):
    safe, _ = demo_seed.assess_target(url)
    assert not safe
    with pytest.raises(demo_seed.Refused):
        demo_seed.guard(url, i_know=False)
    assert "--i-know" in demo_seed.guard(url, i_know=True)


def test_reason_never_contains_the_password():
    url = "postgresql://scribe:s3cretpw@db.example.com/prod"
    with pytest.raises(demo_seed.Refused) as e:
        demo_seed.guard(url, False)
    assert "s3cretpw" not in str(e.value)


def test_non_postgres_url_and_bad_user_ids_refused():
    with pytest.raises(demo_seed.Refused):
        demo_seed.assess_target("mysql://localhost/x")
    for bad in ("", "a b", "eval-golden-v1", "x;drop"):
        with pytest.raises(demo_seed.Refused):
            demo_seed.validate_user_id(bad)
    assert demo_seed.validate_user_id("user_2abcXYZ") == "user_2abcXYZ"


def test_dry_run_is_default_and_needs_no_database(capsys):
    # the host does not exist: a dry run must not try to connect
    rc = demo_seed.main(["--database-url", "postgresql://u:p@nonexistent.invalid/demo_x", "--user-id", "user_abc"])
    out = capsys.readouterr().out
    assert rc == 0 and "DRY RUN" in out and "q3-operations-report.pdf" in out
    assert "FACTS.md" not in out and ".truth.txt" not in out


def test_main_refuses_production_url_before_anything_else(capsys):
    rc = demo_seed.main(["--database-url", "postgresql://u:p@x.neon.tech/neondb", "--user-id", "user_abc", "--apply"])
    assert rc == 2 and "--i-know" in capsys.readouterr().err


def test_apply_without_a_key_stops_before_connecting(monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    rc = demo_seed.main(["--database-url", "postgresql://u:p@nonexistent.invalid/demo_x", "--user-id", "user_abc", "--apply"])
    assert rc == 2 and "GOOGLE_GENERATIVE_AI_API_KEY" in capsys.readouterr().err


def test_fake_embedder_refused_on_a_non_test_database(capsys):
    rc = demo_seed.main(["--database-url", "postgresql://scribe:scribe@localhost:54329/demo_x", "--user-id", "user_abc",
                         "--apply", "--embedder", "fake"])
    assert rc == 2 and "fake embedder" in capsys.readouterr().err.lower()


def test_unknown_only_name_refused():
    with pytest.raises(demo_seed.Refused):
        demo_seed.corpus_files(demo_seed.DEFAULT_CORPUS, ["nope.pdf"])


@needs_db
def test_seed_is_idempotent_and_protects_existing_accounts():
    from _db import aconnect, purge_workspaces
    from scribe_ingest.embedding import FakeEmbedder

    files = demo_seed.corpus_files(demo_seed.DEFAULT_CORPUS, ["glossary.txt", "runbook-deploy.md"])
    user = "user_t33_demo_seed"

    async def counts(conn, wid):
        out = {}
        for t in ("files", "source_versions", "chunks", "chunk_embeddings_1536"):
            out[t] = (await (await conn.execute(f"select count(*) from {t} where workspace_id = %s", (wid,))).fetchone())[0]  # noqa: S608
        return out

    async def go():
        conn = await aconnect(DATABASE_URL_TEST)
        try:
            wid, r1 = await demo_seed.seed(conn, user, files, FakeEmbedder(), None)
            first = await counts(conn, wid)
            wid2, r2 = await demo_seed.seed(conn, user, files, FakeEmbedder(), None)
            second = await counts(conn, wid2)
            kind = (await (await conn.execute("select kind from workspaces where id = %s", (wid,))).fetchone())[0]
            # a different file set in the same account is refused (the account is not new)
            other = demo_seed.corpus_files(demo_seed.DEFAULT_CORPUS, ["changelog.txt"])
            with pytest.raises(demo_seed.Refused):
                await demo_seed.seed(conn, user, other, FakeEmbedder(), None)
            return wid, wid2, kind, r1, r2, first, second
        finally:
            await conn.close()

    wid = None
    try:
        wid, wid2, kind, r1, r2, first, second = asyncio.run(go())
        assert wid == wid2 and kind == "personal"
        assert all(r.status == "ACTIVE" for _, r in r1 + r2)
        assert first == second and first["files"] == 2 and first["chunks"] > 0
        assert first["chunk_embeddings_1536"] > 0
    finally:
        with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
            ids = [str(r[0]) for r in c.execute("select id from workspaces where owner_user_id = %s", (user,)).fetchall()]
        purge_workspaces(DATABASE_URL_TEST, ids)
