"""T15: Postgres RLS workspace isolation (INV-1). Requires DATABASE_URL_TEST.

Everything runs on the superuser test connection with `SET SESSION AUTHORIZATION scribe_app` (a NOBYPASSRLS, non-owner role), which
exercises the same policies the runtime role gets. Rows are committed so they are visible across connections and are
removed by the autouse workspace purge in tests/conftest.py (plus explicit cleanup below).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

from conftest import DATABASE_URL_TEST, needs_db
from _db import fail_or_skip_foreign_jobs, purge_workspaces

pytestmark = needs_db

MIGRATION = Path(__file__).resolve().parents[3] / "drizzle" / "0013_rls_workspace_isolation.sql"

# Tables protected by a direct workspace_id column, and the inherited ones.
DIRECT = ["files", "chats", "rag_traces", "source_versions", "index_generations", "active_indexes", "document_blocks",
          "chunks", "chunk_source_spans", "chunk_embeddings_1536", "chunk_embeddings_3072", "retrieval_traces",
          "retrieval_candidates", "evidence_bundles", "evidence_items", "claim_verifications", "workspace_members"]
INHERITED = ["chat_messages", "ingestion_jobs", "documents", "rag_trace_chunks", "rag_evaluations"]
ALL_PROTECTED = DIRECT + INHERITED + ["workspaces"]


def _connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL_TEST, autocommit=True)


DRIZZLE_DIR = MIGRATION.parent


def _later_migrations() -> list[Path]:
    """Numbered migrations after 0013 (top-level drizzle/ only; deferred/ and rollback/ are subdirs and excluded),
    in sorted order, mirroring scripts/apply-migrations.sh. Replaying 0013 alone would re-create functions with
    their old bodies and clobber later migrations."""
    out = []
    for p in sorted(DRIZZLE_DIR.glob("*.sql")):
        num = p.name.split("_", 1)[0]
        if num.isdigit() and int(num) > 13:
            out.append(p)
    return out


@pytest.fixture(scope="module", autouse=True)
def migrated():
    with _connect() as c:
        c.execute(MIGRATION.read_text().replace("--> statement-breakpoint", ""))
        for p in _later_migrations():
            c.execute(p.read_text().replace("--> statement-breakpoint", ""))


@pytest.fixture(scope="module")
def world(migrated):
    """Two users, each with a workspace and one row in every table we can seed without profile data."""
    tag = uuid.uuid4().hex[:8]
    users = {"a": f"rls-a-{tag}", "b": f"rls-b-{tag}"}
    out: dict[str, dict] = {}
    with _connect() as c:
        for k, user in users.items():
            wid = c.execute("insert into workspaces(kind, owner_user_id) values ('personal', %s) returning id",
                            (user,)).fetchone()[0]
            c.execute("insert into workspace_members(workspace_id, user_id) values (%s,%s)", (wid, user))
            fid = c.execute("insert into files(name,type,size,user_id,workspace_id) values ('f','text/plain',1,%s,%s) "
                            "returning id", (user, wid)).fetchone()[0]
            c.execute("insert into ingestion_jobs(file_id) values (%s)", (fid,))
            cid = f"chat-{k}-{tag}"
            c.execute("insert into chats(id,title,user_id,workspace_id) values (%s,'t',%s,%s)", (cid, user, wid))
            c.execute("insert into chat_messages(id,chat_id,role,content) values (%s,%s,'user','hi')", (f"m-{k}-{tag}", cid))
            tid = f"trace-{k}-{tag}"
            c.execute("insert into rag_traces(id,chat_id,user_id,workspace_id,query,top_k,threshold) "
                      "values (%s,%s,%s,%s,'q',5,0.3)", (tid, cid, user, wid))
            c.execute("insert into rag_trace_chunks(trace_id,document_id,file_id,file_name,chunk_index,similarity,rank,"
                      "content_preview) values (%s,1,%s,'f',0,0.5,1,'x')", (tid, fid))
            c.execute("insert into rag_evaluations(trace_id,judge_model) values (%s,'j')", (tid,))
            c.execute("insert into retrieval_traces(workspace_id,query) values (%s,'q')", (wid,))
            vid = c.execute("insert into source_versions(source_id,workspace_id,version_number) values (%s,%s,1) "
                            "returning id", (fid, wid)).fetchone()[0]
            c.execute("insert into documents(file_id,chunk_index,content,embeddings) values (%s,0,'legacy',array_fill(0.1::real, array[3072])::vector)", (fid,))
            ip = c.execute("select id from index_profiles limit 1").fetchone()[0]
            ep = c.execute("select id from embedding_profiles limit 1").fetchone()[0]
            gid = c.execute("insert into index_generations(source_id,source_version_id,index_profile_id,workspace_id) "
                            "values (%s,%s,%s,%s) returning id", (fid, vid, ip, wid)).fetchone()[0]
            c.execute("insert into active_indexes(source_id,index_profile_id,generation_id,workspace_id) "
                      "values (%s,%s,%s,%s)", (fid, ip, gid, wid))
            bid = c.execute("insert into document_blocks(source_version_id,workspace_id,block_index,type,text) "
                            "values (%s,%s,0,'paragraph','b') returning id", (vid, wid)).fetchone()[0]
            chid = c.execute("insert into chunks(generation_id,source_version_id,workspace_id,chunk_index,content,"
                             "content_hash) values (%s,%s,%s,0,'c',%s) returning id", (gid, vid, wid, f"h-{k}-{tag}")).fetchone()[0]
            c.execute("insert into chunk_source_spans(chunk_id,block_id,workspace_id) values (%s,%s,%s)", (chid, bid, wid))
            for dims in (1536, 3072):
                c.execute(f"insert into chunk_embeddings_{dims}(chunk_id,workspace_id,embedding_profile_id,embedding) "
                          f"values (%s,%s,%s,array_fill(0.1::real, array[{dims}])::vector)", (chid, wid, ep))
            rtid = c.execute("select id from retrieval_traces where workspace_id=%s", (wid,)).fetchone()[0]
            c.execute("insert into retrieval_candidates(trace_id,workspace_id,chunk_id) values (%s,%s,%s)", (rtid, wid, chid))
            ebid = c.execute("insert into evidence_bundles(trace_id,workspace_id,answerability) values (%s,%s,'answerable') "
                             "returning id", (rtid, wid)).fetchone()[0]
            c.execute("insert into evidence_items(bundle_id,workspace_id,label,quote,quote_hash) values (%s,%s,'E1','q','h')",
                      (ebid, wid))
            c.execute("insert into claim_verifications(workspace_id,claim_text,status) values (%s,'claim','UNVERIFIED')", (wid,))
            out[k] = {"user": user, "wid": str(wid), "file": fid, "chat": cid, "version": str(vid), "gen": str(gid),
                      "block": str(bid), "chunk": str(chid), "ip": str(ip), "ep": str(ep), "trace": str(rtid),
                      "bundle": str(ebid)}
    yield out
    purge_workspaces(DATABASE_URL_TEST, [v["wid"] for v in out.values()])  # explicit cleanup, dependency-ordered


def _as_app(ctx: str | None):
    """Open a connection whose transaction runs as scribe_app (SET LOCAL SESSION AUTHORIZATION, so SET ROLE cannot escalate) with an optional workspace context.

    The role and context are transaction-local: after commit()/rollback() the connection is a superuser again, so open a
    fresh _as_app() for every transaction. Using it as a context manager closes the connection (psycopg rolls back)."""
    conn = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    conn.execute("set local session authorization scribe_app")
    if ctx is not None:
        conn.execute("select set_config('app.workspace_id', %s, true)", (ctx,))
    return conn


def _count(conn, table: str) -> int:
    return conn.execute(f"select count(*) from {table}").fetchone()[0]


def test_role_is_not_bypassrls_and_not_owner():
    with _as_app(None) as c:
        assert c.execute("select current_user").fetchone()[0] == "scribe_app"
        assert c.execute("select rolbypassrls, rolsuper from pg_roles where rolname = current_user").fetchone() == (False, False)
        owned = c.execute("select count(*) from pg_class where relowner = (select oid from pg_roles where rolname='scribe_app') "
                          "and relnamespace = 'public'::regnamespace").fetchone()[0]
        assert owned == 0
    with _connect() as c:
        assert c.execute("select rolbypassrls from pg_roles where rolname='scribe_definer'").fetchone()[0] is False


def test_every_workspace_table_has_rls_and_both_policy_clauses():
    with _connect() as c:
        rows = c.execute("select c.relname, c.relrowsecurity, c.relforcerowsecurity from pg_class c "
                         "where c.relnamespace='public'::regnamespace and c.relkind='r'").fetchall()
        flags = {r[0]: (r[1], r[2]) for r in rows}
        with_ws = {r[0] for r in c.execute("select table_name from information_schema.columns where table_schema='public' "
                                           "and column_name='workspace_id'").fetchall()}
        for t in with_ws:  # any future workspace_id table without RLS fails this test
            assert flags[t][0], f"{t} lacks ENABLE RLS"
        for t in ALL_PROTECTED:
            assert flags[t] == (True, False), t  # ENABLE only; FORCE is deferred (drizzle/deferred/force_rls.sql)
            pol = c.execute("select qual, with_check from pg_policies where schemaname='public' and tablename=%s "
                            "and policyname='workspace_isolation'", (t,)).fetchone()
            assert pol and "app.workspace_id" in pol[0] and "app.workspace_id" in pol[1], t


@pytest.mark.parametrize("table", ALL_PROTECTED)
def test_no_context_returns_zero_rows(world, table):
    with _as_app(None) as c:
        assert _count(c, table) == 0
    with _as_app("") as c:  # empty string context is also "no context"
        assert _count(c, table) == 0


@pytest.mark.parametrize("table", ALL_PROTECTED)
def test_user_sees_only_own_workspace_rows(world, table):
    a, b = world["a"], world["b"]
    with _as_app(a["wid"]) as c:
        assert _count(c, table) == 1, f"A should see exactly its own {table} row"
    with _as_app(b["wid"]) as c:
        assert _count(c, table) == 1
    with _as_app(str(uuid.uuid4())) as c:  # unknown workspace
        assert _count(c, table) == 0


def test_workspaces_table_exposes_only_current(world):
    with _as_app(world["a"]["wid"]) as c:
        assert [str(r[0]) for r in c.execute("select id from workspaces").fetchall()] == [world["a"]["wid"]]


def test_cross_workspace_reads_by_id_are_empty(world):
    a, b = world["a"], world["b"]
    with _as_app(a["wid"]) as c:
        assert c.execute("select count(*) from files where id = %s", (b["file"],)).fetchone()[0] == 0
        assert c.execute("select count(*) from chats where id = %s", (b["chat"],)).fetchone()[0] == 0
        assert c.execute("select count(*) from files where workspace_id = %s", (b["wid"],)).fetchone()[0] == 0


def test_cross_workspace_updates_and_deletes_touch_nothing(world):
    a, b = world["a"], world["b"]
    with _as_app(a["wid"]) as c:
        assert c.execute("update files set name='pwn' where id = %s", (b["file"],)).rowcount == 0
        assert c.execute("delete from chats where id = %s", (b["chat"],)).rowcount == 0
        assert c.execute("update ingestion_jobs set status='failed' where file_id = %s", (b["file"],)).rowcount == 0
        assert c.execute("delete from chat_messages where chat_id = %s", (b["chat"],)).rowcount == 0
    with _connect() as c:  # confirm via superuser nothing changed
        assert c.execute("select name from files where id=%s", (b["file"],)).fetchone()[0] == "f"
        assert c.execute("select count(*) from chats where id=%s", (b["chat"],)).fetchone()[0] == 1


def test_cannot_write_into_another_workspace(world):
    a, b = world["a"], world["b"]
    stmts = [
        ("insert into files(name,type,size,user_id,workspace_id) values ('x','t',1,'u',%s)", (b["wid"],)),
        ("insert into chats(id,title,user_id,workspace_id) values (%s,'t','u',%s)", (f"x-{uuid.uuid4()}", b["wid"])),
        ("insert into retrieval_traces(workspace_id,query) values (%s,'q')", (b["wid"],)),
        ("insert into source_versions(source_id,workspace_id,version_number) values (%s,%s,9)", (b["file"], b["wid"])),
        ("insert into workspace_members(workspace_id,user_id) values (%s,'evil')", (b["wid"],)),
        ("insert into chat_messages(id,chat_id,role,content) values (%s,%s,'user','x')", (f"x-{uuid.uuid4()}", b["chat"])),
        ("insert into ingestion_jobs(file_id) values (%s)", (b["file"],)),
        ("insert into document_blocks(source_version_id,workspace_id,block_index,type,text) values (%s,%s,7,'p','x')",
         (b["version"], b["wid"])),
        ("insert into chunks(generation_id,source_version_id,workspace_id,chunk_index,content,content_hash) "
         "values (%s,%s,%s,9,'x','hx')", (b["gen"], b["version"], b["wid"])),
        ("insert into chunk_source_spans(chunk_id,block_id,workspace_id) values (%s,%s,%s)", (b["chunk"], b["block"], b["wid"])),
        ("insert into chunk_embeddings_1536(chunk_id,workspace_id,embedding_profile_id,embedding) values "
         "(%s,%s,%s,array_fill(0.1::real, array[1536])::vector)", (b["chunk"], b["wid"], b["ep"])),
        ("insert into index_generations(source_id,source_version_id,index_profile_id,workspace_id) values (%s,%s,%s,%s)",
         (b["file"], b["version"], b["ip"], b["wid"])),
        ("insert into retrieval_candidates(trace_id,workspace_id,chunk_id) values (%s,%s,%s)", (b["trace"], b["wid"], b["chunk"])),
        ("insert into evidence_bundles(trace_id,workspace_id,answerability) values (%s,%s,'x')", (b["trace"], b["wid"])),
        ("insert into evidence_items(bundle_id,workspace_id,label,quote,quote_hash) values (%s,%s,'E9','q','h')",
         (b["bundle"], b["wid"])),
        ("insert into claim_verifications(workspace_id,claim_text,status) values (%s,'c','x')", (b["wid"],)),
        ("insert into documents(file_id,chunk_index,content,embeddings) values (%s,5,'x',array_fill(0.1::real, array[3072])::vector)", (b["file"],)),
    ]
    for sql, params in stmts:
        with _as_app(a["wid"]) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, params)
            c.rollback()


def test_cannot_update_or_delete_foreign_rows_in_new_tables(world):
    a, b = world["a"], world["b"]
    with _as_app(a["wid"]) as c:
        for t in ("document_blocks", "chunks", "chunk_source_spans", "chunk_embeddings_1536", "chunk_embeddings_3072",
                  "index_generations", "active_indexes", "retrieval_candidates", "evidence_bundles", "evidence_items",
                  "claim_verifications"):
            assert c.execute(f"delete from {t} where workspace_id = %s", (b["wid"],)).rowcount == 0, t


def test_null_workspace_id_is_rejected(world):
    with _as_app(world["a"]["wid"]) as c:
        with pytest.raises((psycopg.errors.NotNullViolation, psycopg.errors.InsufficientPrivilege)):
            c.execute("insert into files(name,type,size,user_id,workspace_id) values ('x','t',1,'u',NULL)")


def test_runtime_role_cannot_escalate_or_alter_policies(world):
    for sql in ("set role scribe_definer", "drop policy workspace_isolation on files",
                "alter table files disable row level security", "alter table files no force row level security",
                "alter role scribe_app bypassrls", "create policy p on files using (true)"):
        with _as_app(world["a"]["wid"]) as c:
            with pytest.raises(psycopg.errors.Error) as ei:
                c.execute(sql)
            assert ei.value.sqlstate in ("42501",), sql


def test_runtime_role_cannot_write_eval_or_workspace_tables():
    with _connect() as c:
        for t in ("eval_runs", "eval_results", "workspaces", "workspace_members"):
            assert c.execute("select has_table_privilege('scribe_app', %s, 'SELECT')", (t,)).fetchone()[0], t
            for priv in ("INSERT", "UPDATE", "DELETE"):
                assert not c.execute("select has_table_privilege('scribe_app', %s, %s)", (t, priv)).fetchone()[0], (t, priv)


def test_owner_role_still_sees_rows_while_force_is_deferred(world):
    """A non-superuser, NOBYPASSRLS owner (the Neon owner shape) must keep working: FORCE is not set by 0013."""
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    try:
        c.execute("create role rls_owner_probe nologin nobypassrls")
        c.execute("grant usage on schema public to rls_owner_probe")  # schema ACL varies per environment
        c.execute("alter table files owner to rls_owner_probe")
        c.execute("set local role rls_owner_probe")
        assert c.execute("select count(*) from files").fetchone()[0] >= 2
    finally:
        c.rollback()  # DDL and role creation are transactional
        c.close()


def test_cannot_move_row_into_another_workspace(world):
    a, b = world["a"], world["b"]
    with _as_app(a["wid"]) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update files set workspace_id = %s where id = %s", (b["wid"], a["file"]))
        c.rollback()


def test_own_workspace_writes_still_work(world):
    a = world["a"]
    with _as_app(a["wid"]) as c:
        c.execute("insert into chats(id,title,user_id,workspace_id) values (%s,'t','u',%s)", (f"ok-{uuid.uuid4()}", a["wid"]))
        assert _count(c, "chats") == 2
        c.rollback()


def test_context_does_not_leak_across_transactions(world):
    conn = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    conn.execute("set session authorization scribe_app")
    conn.execute("select set_config('app.workspace_id', %s, true)", (world["a"]["wid"],))
    assert _count(conn, "files") == 1
    conn.commit()  # transaction-local setting is gone
    assert _count(conn, "files") == 0
    conn.close()


# --- SECURITY DEFINER functions ------------------------------------------------------------------------------

def test_definer_functions_are_hardened():
    with _connect() as c:
        rows = c.execute("select p.proname, p.prosecdef, p.proconfig, r.rolname, "
                         "has_function_privilege('public', p.oid, 'EXECUTE'), has_function_privilege('scribe_app', p.oid, 'EXECUTE') "
                         "from pg_proc p join pg_roles r on r.oid=p.proowner where p.pronamespace='public'::regnamespace "
                         "and p.proname like 'app\\_%'").fetchall()
    assert {r[0] for r in rows} == {"app_workspace_ids_for_user", "app_ensure_personal_workspace",
                                    "app_claim_ingestion_job", "app_sweep_expired_leases", "app_list_purgeable_sources",
                                    "app_count_live_leases", "app_count_claimable_jobs"}  # last two: 0014 (T18)
    for name, secdef, cfg, owner, pub, app in rows:
        assert secdef and owner == "scribe_definer" and not pub and app, name
        assert cfg == ["search_path=pg_catalog, public"], (name, cfg)


def test_workspace_lookup_and_ensure_via_definer(world):
    a, b = world["a"], world["b"]
    with _as_app(None) as c:  # no context at all: the lookup must still work
        assert [str(r[0]) for r in c.execute("select * from app_workspace_ids_for_user(%s)", (a["user"],)).fetchall()] == [a["wid"]]
        assert c.execute("select count(*) from app_workspace_ids_for_user('nobody')").fetchone()[0] == 0
        assert c.execute("select count(*) from app_workspace_ids_for_user('')").fetchone()[0] == 0
        assert str(c.execute("select app_ensure_personal_workspace(%s)", (b["user"],)).fetchone()[0]) == b["wid"]
        c.rollback()
    new_user = f"rls-new-{uuid.uuid4().hex[:8]}"
    with _as_app(None) as c:
        wid = c.execute("select app_ensure_personal_workspace(%s)", (new_user,)).fetchone()[0]
        assert c.execute("select app_ensure_personal_workspace(%s)", (new_user,)).fetchone()[0] == wid  # idempotent
        assert [r[0] for r in c.execute("select * from app_workspace_ids_for_user(%s)", (new_user,)).fetchall()] == [wid]
        c.rollback()
    with _as_app(None) as c:
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("select app_ensure_personal_workspace('')")


def _queue_job(world, key: str, **set_sql) -> int:
    with _connect() as c:
        jid = c.execute("select id from ingestion_jobs where file_id=%s", (world[key]["file"],)).fetchone()[0]
        c.execute("update ingestion_jobs set status='queued', next_retry_at=null, lease_token=null, lease_expires_at=null, "
                  "attempts=0 where file_id=%s", (world[key]["file"],))
        return jid


def _park_other_jobs(c, keep_ids):
    """Other test modules leave no rows (purged), but be defensive: only our jobs may be claimable."""
    foreign = c.execute("select count(*) from ingestion_jobs where id <> all(%s) and status in ('queued','retrying')",
                        (list(keep_ids),)).fetchone()[0]
    if foreign:
        fail_or_skip_foreign_jobs()


def test_claim_job_returns_ids_only_with_skip_locked(world):
    ja, jb = _queue_job(world, "a"), _queue_job(world, "b")
    with _connect() as c:
        _park_other_jobs(c, [ja, jb])
    c1 = _as_app(None)  # no workspace context, as the queue runner has none
    claimed1 = c1.execute("select * from app_claim_ingestion_job(120)").fetchall()
    assert len(claimed1) == 1
    jid1, fid1, wid1, lease1 = claimed1[0]
    assert {jid1} <= {ja, jb} and lease1 is not None
    assert str(wid1) in (world["a"]["wid"], world["b"]["wid"])
    # a second concurrent claimer, while the first transaction still holds its row lock, gets the OTHER job (SKIP LOCKED)
    c2 = _as_app(None)
    claimed2 = c2.execute("select * from app_claim_ingestion_job(120)").fetchall()
    assert len(claimed2) == 1 and claimed2[0][0] != jid1
    # a third claimer finds nothing runnable
    c3 = _as_app(None)
    assert c3.execute("select * from app_claim_ingestion_job(120)").fetchall() == []
    for c in (c1, c2, c3):
        c.rollback(); c.close()
    # rolled back: both queued again; commit one claim and verify state through the superuser
    with _as_app(None) as c:
        jid, _, _, lease = c.execute("select * from app_claim_ingestion_job(120)").fetchone()
        c.commit()
    with _connect() as c:
        st, att, tok, exp = c.execute("select status, attempts, lease_token, lease_expires_at > now() from ingestion_jobs "
                                      "where id=%s", (jid,)).fetchone()
        assert (st, att, tok, exp) == ("processing", 1, lease, True)


def test_claim_reclaims_expired_lease_and_sweep_releases_it(world):
    ja, jb = _queue_job(world, "a"), _queue_job(world, "b")
    with _connect() as c:
        _park_other_jobs(c, [ja, jb])
        c.execute("update ingestion_jobs set status='processing', lease_token=gen_random_uuid(), "
                  "lease_expires_at = now() - interval '1 minute' where id = %s", (ja,))
        c.execute("update ingestion_jobs set status='completed' where id = %s", (jb,))
    with _as_app(None) as c:
        released = [r[0] for r in c.execute("select * from app_sweep_expired_leases()").fetchall()]
        assert released == [ja]
        c.commit()
    with _connect() as c:
        assert c.execute("select status, lease_token from ingestion_jobs where id=%s", (ja,)).fetchone() == ("queued", None)
    with _as_app(None) as c:
        assert c.execute("select job_id from app_claim_ingestion_job(60)").fetchone()[0] == ja
    with _as_app(None) as c:  # fresh scribe_app transaction (the previous one was rolled back)
        assert c.execute("select * from app_sweep_expired_leases()").fetchall() == []


def test_claim_respects_max_attempts_deleted_sources_and_clamps_lease(world):
    ja, jb = _queue_job(world, "a"), _queue_job(world, "b")
    with _connect() as c:
        _park_other_jobs(c, [ja, jb])
        c.execute("update ingestion_jobs set attempts = 5 where id = %s", (ja,))  # exhausted
        c.execute("update files set deleted_at = now() where id = %s", (world["b"]["file"],))  # deleted source
    try:
        with _as_app(None) as c:
            assert c.execute("select * from app_claim_ingestion_job(60)").fetchall() == []
        with _connect() as c:
            c.execute("update files set deleted_at = null where id = %s", (world["b"]["file"],))
        with _as_app(None) as c:
            jid, _, _, _ = c.execute("select * from app_claim_ingestion_job(999999)").fetchone()
            assert jid == jb
            c.execute("reset session authorization")  # inspect as superuser (scribe_app cannot see the table directly)
            secs = c.execute("select extract(epoch from lease_expires_at - now()) from ingestion_jobs where id=%s",
                             (jid,)).fetchone()[0]
            assert 3500 < secs <= 3600  # clamped to 3600
        with _as_app(None) as c:  # an exhausted job with an expired lease is failed by the sweep, not requeued
            c.execute("reset session authorization")  # superuser setup inside the same transaction
            c.execute("update ingestion_jobs set status='processing', lease_expires_at=now()-interval '1 minute', "
                      "attempts=5 where id=%s", (ja,))
            c.execute("set local session authorization scribe_app")
            assert [r[0] for r in c.execute("select * from app_sweep_expired_leases(5)").fetchall()] == [ja]
            assert c.execute("select status from ingestion_jobs where id=%s", (ja,)).fetchone() is None  # still hidden
            c.execute("reset session authorization")
            assert c.execute("select status, error_code from ingestion_jobs where id=%s", (ja,)).fetchone() == (
                "failed", "lease_expired_max_attempts")
    finally:
        with _connect() as c:
            c.execute("update files set deleted_at = null where id in (%s,%s)",
                      (world["a"]["file"], world["b"]["file"]))


def test_purgeable_sources_sweep_returns_ids_only(world):
    a, b = world["a"], world["b"]
    with _connect() as c:
        c.execute("update files set deleted_at = now() - interval '2 days' where id = %s", (a["file"],))
        c.execute("update files set deleted_at = now() where id = %s", (b["file"],))
    try:
        with _as_app(None) as c:
            rows = c.execute("select * from app_list_purgeable_sources(86400, 1000)").fetchall()
            assert [(r[0], str(r[1])) for r in rows if r[0] in (a["file"], b["file"])] == [(a["file"], a["wid"])]
            assert _count(c, "files") == 0  # the definer function did not widen the caller's own visibility
    finally:
        with _connect() as c:
            c.execute("update files set deleted_at = null where id in (%s,%s)", (a["file"], b["file"]))


def test_queue_table_is_hidden_directly_but_reachable_through_the_function(world):
    _queue_job(world, "a"), _queue_job(world, "b")
    with _as_app(None) as c:
        assert _count(c, "ingestion_jobs") == 0
        assert c.execute("select count(*) from app_claim_ingestion_job(60)").fetchone()[0] == 1
