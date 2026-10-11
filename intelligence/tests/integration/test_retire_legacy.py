"""T22: drizzle/deferred/retire_legacy.sql runs ONLY against a throwaway database built here (never the shared one)."""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import psycopg
import pytest
from _db import REPO, aconnect, new_workspace, purge_workspaces
from _legacy import add_structural, legacy_source
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = [needs_db, pytest.mark.skipif(not (shutil.which("psql") and shutil.which("npx")), reason="psql/npx missing")]
SQL = REPO / "drizzle" / "deferred" / "retire_legacy.sql"


def _swap_db(url: str, name: str) -> str:
    return url.rsplit("/", 1)[0] + "/" + name


@pytest.fixture(scope="module")
def fresh_db():
    """A database built from scratch by scripts/apply-migrations.sh. The name contains 'test'; dropped afterwards."""
    name = f"t22_retire_test_{uuid.uuid4().hex[:8]}"
    assert "test" in name
    url = _swap_db(DATABASE_URL_TEST, name)
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    try:
        subprocess.run([str(REPO / "scripts" / "apply-migrations.sh"), url], check=True, capture_output=True, text=True,
                       cwd=REPO, timeout=300)
        yield url
    finally:
        with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')


def run_sql(url: str, *, dry_run=False):
    cmd = ["psql", url, "-v", "ON_ERROR_STOP=1", "-q"] + (["-v", "dry_run=1"] if dry_run else []) + ["-f", str(SQL)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


@pytest.fixture()
async def conn(fresh_db):
    c = await aconnect(fresh_db)
    # a clean slate per test, in the throwaway DB only
    cur = await c.execute("select id from workspaces")
    purge_workspaces(fresh_db, [str(r[0]) for r in await cur.fetchall()])
    yield c
    await c.close()


async def n3072(conn, fid):
    cur = await conn.execute("select count(*) from chunk_embeddings_3072 e join chunks c on c.id=e.chunk_id "
                             "join index_generations g on g.id=c.generation_id where g.source_id=%s", (fid,))
    return (await cur.fetchone())[0]


async def legacy_state(conn, fid):
    cur = await conn.execute(
        "select g.status, (select count(*) from active_indexes a where a.generation_id=g.id), "
        "(select count(*) from chunks c where c.generation_id=g.id) from index_generations g "
        "join index_profiles ip on ip.id=g.index_profile_id where g.source_id=%s and ip.name='legacy-v1'", (fid,))
    return await cur.fetchone()


async def test_guard_refuses_when_a_source_is_not_upgraded(conn, fresh_db):
    wid = await new_workspace(conn, kind="personal")
    done = await legacy_source(conn, wid, name="done.md")
    await add_structural(conn, wid, done)
    todo = await legacy_source(conn, wid, name="todo.md")  # neither upgraded nor marked re-upload
    r = run_sql(fresh_db)
    assert r.returncode != 0 and "retire_legacy refused" in r.stderr and str(todo) in r.stderr
    assert await n3072(conn, done) == 1 and await n3072(conn, todo) == 1  # nothing deleted


async def test_guard_refuses_a_half_embedded_or_building_structural_generation(conn, fresh_db):
    wid = await new_workspace(conn, kind="personal")
    half = await legacy_source(conn, wid, name="half.md")
    await add_structural(conn, wid, half, status="READY", expected=3, embedded=1)
    r = run_sql(fresh_db)
    assert r.returncode != 0 and "retire_legacy refused" in r.stderr
    assert await n3072(conn, half) == 1


async def test_dry_run_rolls_back(conn, fresh_db):
    wid = await new_workspace(conn, kind="personal")
    fid = await legacy_source(conn, wid)
    await add_structural(conn, wid, fid)
    r = run_sql(fresh_db, dry_run=True)
    assert r.returncode == 0, r.stderr
    assert await n3072(conn, fid) == 1 and (await legacy_state(conn, fid))[0] == "ACTIVE"


async def test_passes_when_all_upgraded_or_marked_and_only_deletes_what_it_should(conn, fresh_db):
    wid = await new_workspace(conn, kind="personal")
    up = await legacy_source(conn, wid, name="up.md")
    await add_structural(conn, wid, up)
    ready = await legacy_source(conn, wid, name="ready.md")  # READY (not yet ACTIVE) and fully embedded also counts
    await add_structural(conn, wid, ready, status="READY")
    marked = await legacy_source(conn, wid, name="marked.md", storage_url=None)
    await conn.execute("update files set reupload_required_at=now(), reupload_reason='NO_BLOB' where id=%s", (marked,))
    gone = await legacy_source(conn, wid, name="gone.md")
    await conn.execute("update files set deleted_at=now() where id=%s", (gone,))  # deleted sources do not block
    ev = await new_workspace(conn, kind="eval")
    baseline = await legacy_source(conn, ev, name="eval.md")  # eval workspace keeps its legacy vectors, no guard

    r = run_sql(fresh_db)
    assert r.returncode == 0, r.stderr
    assert "2 Sources upgraded" in r.stderr and "1 Sources kept on legacy" in r.stderr
    for fid in (up, ready):
        assert await n3072(conn, fid) == 0
        assert await legacy_state(conn, fid) == ("SUPERSEDED", 0, 1)  # chunks kept for historical citations (D20)
        cur = await conn.execute("select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id "
                                 "where c.source_version_id=(select active_version_id from files where id=%s)", (fid,))
        assert (await cur.fetchone())[0] == 1  # the new index is untouched
    for fid in (marked, baseline, gone):
        assert await n3072(conn, fid) == 1
        assert (await legacy_state(conn, fid)) == ("ACTIVE", 1, 1)
    r2 = run_sql(fresh_db)  # a second run is a harmless no-op
    assert r2.returncode == 0 and "0 legacy embedding rows deleted" in r2.stderr


def test_the_destructive_documents_drop_is_commented_out_and_the_file_is_not_journaled():
    text = SQL.read_text()
    live = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    assert "DROP TABLE" not in live.upper()
    journal = (REPO / "drizzle" / "meta" / "_journal.json").read_text()
    assert "retire_legacy.sql" not in journal and '"retire_legacy"' not in journal
    assert SQL.parent.name == "deferred"
