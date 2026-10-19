"""Migration 0020: the compat shim that lets the Scribe 1.0 build (commit 0edddb1) keep inserting files, chats and rag_traces
after 0008 made workspace_id NOT NULL. Requires DATABASE_URL_TEST (migrations applied, e.g. scripts/apply-migrations.sh).

Every test runs in one transaction on one superuser connection that is rolled back at the end, except the two concurrency
tests, which commit and purge their workspaces. The "legacy build" is the owner connection with no workspace context: 1.0
connects as the table owner and never sets app.workspace_id. Column sets below are copied from the 1.0 write paths
(`git show 0edddb1:<path>`); Drizzle also lists the omitted columns as `default`, which is the same as omitting them.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import psycopg
import pytest
from _db import purge_workspaces
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db

DRIZZLE = Path(__file__).resolve().parents[3] / "drizzle"
MIGRATION = DRIZZLE / "0020_legacy_workspace_default.sql"
ROLLBACK = DRIZZLE / "rollback" / "0020_legacy_workspace_default.down.sql"
TRIGGERS = ("files_legacy_default_workspace", "chats_legacy_default_workspace", "rag_traces_legacy_default_workspace")


@pytest.fixture()
def conn():
    with psycopg.connect(DATABASE_URL_TEST, prepare_threshold=None) as c:
        try:
            yield c
        finally:
            c.rollback()


def new_user() -> str:
    return f"t20-{uuid.uuid4()}"


def personal_ids(c, user) -> list:
    return [r[0] for r in c.execute("select id from workspaces where kind='personal' and owner_user_id=%s", (user,)).fetchall()]


def the_personal_workspace(c, user):
    """Exactly one personal workspace for the user, with exactly one 'owner' member row (the user). Returns its id."""
    ids = personal_ids(c, user)
    assert len(ids) == 1, ids
    members = c.execute("select user_id, role from workspace_members where workspace_id=%s", (ids[0],)).fetchall()
    assert members == [(user, "owner")], members
    return ids[0]


def as_app(c, ctx=None):
    """Switch the CURRENT transaction to scribe_app (SET LOCAL SESSION AUTHORIZATION) with an optional workspace context."""
    c.execute("set local session authorization scribe_app")
    if ctx is not None:
        c.execute("select set_config('app.workspace_id', %s, true)", (str(ctx),))


def fresh_app_connection(ctx=None) -> psycopg.Connection:
    """Same as test_rls._as_app: a new connection whose transaction runs as scribe_app."""
    c = psycopg.connect(DATABASE_URL_TEST, autocommit=False)
    c.execute("set local session authorization scribe_app")
    if ctx is not None:
        c.execute("select set_config('app.workspace_id', %s, true)", (str(ctx),))
    return c


# --- the 1.0 write paths, exact column sets -------------------------------------------------------------------------
FILE_INSERTS = {
    # 0edddb1 lib/ingestion/source-intake.ts:82 (reserveSourceUpload, Drizzle insert + returning)
    "source_intake_reserve": (
        'insert into "files" ("name","size","type","user_id","status","updated_at") '
        "values ('a.txt', 10, 'text/plain', %(u)s, 'uploading', now()) returning id, workspace_id"),
    # 0edddb1 app/api/uploads/complete/route.ts:66 (insertQueuedFileRecord, Drizzle)
    "uploads_complete": (
        'insert into "files" ("name","type","size","user_id","file_data","storage_key","storage_url","status") '
        "values ('b.txt', 'text/plain', 11, %(u)s, NULL, 'k/b.txt', 'https://blob.example/b.txt', 'queued') returning id, workspace_id"),
    # 0edddb1 app/api/uploads/complete/route.ts:89 (raw-SQL fallback of the same insert)
    "uploads_complete_raw": (
        'insert into "files" ("name", "type", "size", "user_id", "file_data", "storage_key", "storage_url", "status") '
        "values ('c.txt', 'text/plain', 12, %(u)s, NULL, 'k/c.txt', 'https://blob.example/c.txt', 'queued') "
        'returning "id", workspace_id, "name", "type", "size", "user_id", "file_data", "extracted_text", "storage_key", "storage_url", '
        '"status", "processing_error", "text_bytes", "created_at"'),
    # 0edddb1 app/api/ingest/batch/init/route.ts:59 (initBrowserFile)
    "batch_init": (
        'insert into "files" ("name","type","size","user_id","status") '
        "values ('d.txt', 'text/plain', 13, %(u)s, 'processing') returning id, workspace_id"),
}


def legacy_file(c, user, which="source_intake_reserve") -> tuple[int, object]:
    return c.execute(FILE_INSERTS[which], {"u": user}).fetchone()[:2]


def legacy_chat(c, user) -> str:
    """0edddb1 app/chat/actions.ts:47 createChat: chats(id, title, user_id)."""
    cid = f"t20-chat-{uuid.uuid4()}"
    c.execute('insert into "chats" ("id","title","user_id") values (%s,%s,%s)', (cid, "New chat", user))
    return cid


def legacy_trace(c, chat_id, user, user_message_id=None) -> str:
    """0edddb1 lib/rag-observability.ts:127 recordRagTrace: rag_traces(id,chat_id,user_id,user_message_id,query,status,
    embedding_ms,retrieval_ms,total_ms,top_k,threshold)."""
    tid = f"t20-trace-{uuid.uuid4()}"
    c.execute('insert into "rag_traces" ("id","chat_id","user_id","user_message_id","query","status","embedding_ms",'
              '"retrieval_ms","total_ms","top_k","threshold") values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
              (tid, chat_id, user, user_message_id, "what is x?", "retrieved", 12, 34, 46, 5, 0.3))
    return tid


def workspace_of(c, table, key_col, key):
    return c.execute(f"select workspace_id from {table} where {key_col}=%s", (key,)).fetchone()[0]


# --- a. replay every 1.0 write path as the owner with no context -----------------------------------------------------
@pytest.mark.parametrize("which", list(FILE_INSERTS))
def test_legacy_file_inserts_land_in_the_users_personal_workspace(conn, which):
    user = new_user()
    fid, wid = legacy_file(conn, user, which)
    assert wid is not None and wid == the_personal_workspace(conn, user)
    assert workspace_of(conn, "files", "id", fid) == wid


def test_legacy_create_chat_lands_in_the_users_personal_workspace(conn):
    user = new_user()
    cid = legacy_chat(conn, user)
    assert workspace_of(conn, "chats", "id", cid) == the_personal_workspace(conn, user)


def test_legacy_chat_flow_messages_trace_chunks_evaluation_usage(conn):
    """chat -> pre-saved user message -> trace -> trace chunks -> completed trace -> assistant message -> evaluation upsert
    -> usage event: the whole 1.0 chat request, every statement with the 1.0 column set."""
    user = new_user()
    fid, _ = legacy_file(conn, user)
    cid = legacy_chat(conn, user)
    umid, amid = f"t20-um-{uuid.uuid4()}", f"t20-am-{uuid.uuid4()}"
    # 0edddb1 app/api/chat/route.ts:187 (pre-save the user message)
    conn.execute('insert into "chat_messages" ("id","chat_id","role","content","parts") values (%s,%s,%s,%s,%s::jsonb)',
                 (umid, cid, "user", "what is x?", json.dumps([{"type": "text", "text": "what is x?"}])))
    tid = legacy_trace(conn, cid, user, umid)
    # 0edddb1 lib/rag-observability.ts:143 (insert(ragTraceChunks), 1.0 columns incl. metadata)
    conn.execute('insert into "rag_trace_chunks" ("trace_id","document_id","file_id","file_name","chunk_index","similarity",'
                 '"rank","content_preview","metadata") values (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)',
                 (tid, 1, fid, "a.txt", 0, 0.5, 1, "preview", json.dumps({"page": 1})))
    # 0edddb1 app/api/chat/route.ts:344 (assistant message after the stream)
    conn.execute('insert into "chat_messages" ("id","chat_id","role","content","parts") values (%s,%s,%s,%s,%s::jsonb)',
                 (amid, cid, "assistant", "x is y", json.dumps([{"type": "text", "text": "x is y"}])))
    # completeRagTrace (UPDATE, not an insert, but part of the same request)
    assert conn.execute("update rag_traces set assistant_message_id=%s, generation_ms=5, total_ms=9, status='completed' where id=%s",
                        (amid, tid)).rowcount == 1
    # 0edddb1 lib/rag-observability.ts:169 (insert(ragEvaluations) ... onConflictDoUpdate target trace_id), twice
    for score in (0.5, 0.9):
        conn.execute('insert into "rag_evaluations" ("trace_id","status","judge_model","groundedness_score","answer_relevance_score",'
                     '"citation_support_score","overall_score","verdict","rationale","error") values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                     'on conflict ("trace_id") do update set "status"=excluded."status","judge_model"=excluded."judge_model",'
                     '"groundedness_score"=excluded."groundedness_score","answer_relevance_score"=excluded."answer_relevance_score",'
                     '"citation_support_score"=excluded."citation_support_score","overall_score"=excluded."overall_score",'
                     '"verdict"=excluded."verdict","rationale"=excluded."rationale","error"=excluded."error"',
                     (tid, "completed", "judge", score, score, score, score, "pass", "ok", None))
    assert conn.execute("select count(*), max(overall_score) from rag_evaluations where trace_id=%s", (tid,)).fetchone() == (1, 0.9)
    # 0edddb1 lib/billing/usage.ts:31 (insert(usageEvents) ... onConflictDoNothing target idempotency_key), twice
    key = f"t20-{uuid.uuid4()}"
    for _ in range(2):
        conn.execute('insert into "usage_events" ("user_id","metric","quantity","unit","source_type","source_id","idempotency_key",'
                     '"is_estimated","occurred_at") values (%s,%s,%s,%s,%s,%s,%s,%s,now()) on conflict ("idempotency_key") do nothing',
                     (user, "model_input_tokens", 100, "tokens", "chat", cid, key, False))
    assert conn.execute("select count(*) from usage_events where idempotency_key=%s", (key,)).fetchone()[0] == 1

    wid = the_personal_workspace(conn, user)
    assert workspace_of(conn, "files", "id", fid) == workspace_of(conn, "chats", "id", cid) == workspace_of(conn, "rag_traces", "id", tid) == wid
    assert conn.execute("select count(*) from chat_messages where chat_id=%s", (cid,)).fetchone()[0] == 2


def test_legacy_ingestion_job_and_documents_inserts(conn):
    user = new_user()
    fid, wid = legacy_file(conn, user, "uploads_complete")
    # 0edddb1 app/api/uploads/complete/route.ts:241 (enqueueIngestionJob, Drizzle insert)
    conn.execute('insert into "ingestion_jobs" ("file_id","status","attempts") values (%s,%s,%s)', (fid, "queued", 0))
    # 0edddb1 lib/ingestion/source-intake.ts:266 (insert(documents), 1.0 columns, legacy 3072-d embedding)
    conn.execute('insert into "documents" ("file_id","chunk_index","content","metadata","embeddings") '
                 "values (%s,%s,%s,%s::jsonb,array_fill(0.1::real, array[3072])::vector)", (fid, 0, "legacy chunk", json.dumps({"chunkIndex": 0})))
    # 0edddb1 app/api/ingest/batch/route.ts:95 re-inserts documents with ON CONFLICT (file_id, chunk_index) DO NOTHING
    conn.execute('insert into "documents" ("file_id","chunk_index","content","metadata","embeddings") '
                 "values (%s,%s,%s,%s::jsonb,array_fill(0.1::real, array[3072])::vector) on conflict (\"file_id\",\"chunk_index\") do nothing",
                 (fid, 0, "legacy chunk", json.dumps({"chunkIndex": 0})))
    assert conn.execute("select count(*) from documents where file_id=%s", (fid,)).fetchone()[0] == 1
    assert wid == the_personal_workspace(conn, user)


SOURCE_INTAKE_QUEUE = (
    # 0edddb1 lib/ingestion/source-intake.ts:177-192 transitionToQueued (raw INSERT INTO "ingestion_jobs" ... ON CONFLICT ("file_id"))
    'WITH source AS (UPDATE "files" SET "storage_key" = \'k/a\', "storage_url" = \'https://blob.example/a\', "status" = \'queued\', '
    '"processing_error" = NULL, "updated_at" = now() WHERE "id" = %(id)s AND "user_id" = %(u)s AND "status" = \'uploading\' RETURNING "id") '
    'INSERT INTO "ingestion_jobs" ("file_id", "status", "attempts", "created_at", "updated_at") '
    "SELECT \"id\", 'queued', 0, now(), now() FROM source ON CONFLICT (\"file_id\") DO NOTHING RETURNING \"file_id\"")


@pytest.mark.xfail(strict=True, raises=psycopg.errors.InvalidColumnReference,
                   reason="KNOWN GAP, not fixed by 0020: 0014 dropped the unique index on ingestion_jobs(file_id) (jobs are per source "
                          "version now), so 1.0's ON CONFLICT (file_id) has no arbiter index. Needs a decision, see docs/scribe2/neon-rehearsal.md.")
def test_legacy_source_intake_queue_transition_with_on_conflict(conn):
    user = new_user()
    fid, _ = legacy_file(conn, user, "source_intake_reserve")
    assert conn.execute(SOURCE_INTAKE_QUEUE, {"id": fid, "u": user}).fetchall() == [(fid,)]


def test_legacy_source_intake_queue_transition_without_on_conflict_clause(conn):
    """The same statement minus the ON CONFLICT clause: everything the shim is responsible for (the files UPDATE and the
    job INSERT on a 1.0-created file) works."""
    user = new_user()
    fid, _ = legacy_file(conn, user, "source_intake_reserve")
    sql = SOURCE_INTAKE_QUEUE.replace(' ON CONFLICT ("file_id") DO NOTHING', "")
    assert conn.execute(sql, {"id": fid, "u": user}).fetchall() == [(fid,)]
    assert conn.execute("select status from files where id=%s", (fid,)).fetchone()[0] == "queued"


# --- b. existing personal workspace is reused ---------------------------------------------------------------------
def test_existing_personal_workspace_is_reused_not_duplicated(conn):
    user = new_user()
    existing = conn.execute("select app_ensure_personal_workspace(%s)", (user,)).fetchone()[0]
    fid, wid = legacy_file(conn, user)
    cid = legacy_chat(conn, user)
    tid = legacy_trace(conn, cid, user)
    assert wid == existing
    assert workspace_of(conn, "chats", "id", cid) == existing == workspace_of(conn, "rag_traces", "id", tid)
    assert the_personal_workspace(conn, user) == existing


def test_two_legacy_users_get_two_separate_workspaces(conn):
    a, b = new_user(), new_user()
    (_, wa), (_, wb) = legacy_file(conn, a), legacy_file(conn, b)
    assert wa != wb and wa == the_personal_workspace(conn, a) and wb == the_personal_workspace(conn, b)


# --- c. 2.0 inserts with an explicit workspace_id are untouched -------------------------------------------------------
def test_explicit_workspace_id_is_untouched_and_creates_no_personal_workspace(conn):
    user = new_user()
    ev = conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id", (f"t20-eval-{uuid.uuid4()}",)).fetchone()[0]
    fid = conn.execute("insert into files(name,type,size,user_id,workspace_id,status) values ('e','text/plain',1,%s,%s,'ready') returning id", (user, ev)).fetchone()[0]
    cid = f"t20-chat-{uuid.uuid4()}"
    conn.execute("insert into chats(id,title,user_id,workspace_id) values (%s,'t',%s,%s)", (cid, user, ev))
    tid = f"t20-trace-{uuid.uuid4()}"
    conn.execute("insert into rag_traces(id,chat_id,user_id,workspace_id,query,top_k,threshold) values (%s,%s,%s,%s,'q',3,0.1)", (tid, cid, user, ev))
    assert workspace_of(conn, "files", "id", fid) == workspace_of(conn, "chats", "id", cid) == workspace_of(conn, "rag_traces", "id", tid) == ev
    assert personal_ids(conn, user) == []


# --- d. with a workspace context a missing workspace_id still fails loudly -----------------------------------------
INSERT_NULL_WS = {
    "files": "insert into files(name,type,size,user_id,workspace_id) values ('x','text/plain',1,%(u)s,NULL)",
    "chats": "insert into chats(id,title,user_id,workspace_id) values ('t20-' || gen_random_uuid()::text,'t',%(u)s,NULL)",
    "rag_traces": "insert into rag_traces(id,chat_id,user_id,workspace_id,query,top_k,threshold) values ('t20-' || gen_random_uuid()::text,'c',%(u)s,NULL,'q',3,0.1)",
}
LEGACY_SHAPED = {  # same tables, workspace_id omitted entirely (what 1.0 sends); rag_traces needs a chat, see below
    "files": "insert into files(name,type,size,user_id) values ('x','text/plain',1,%(u)s)",
    "chats": "insert into chats(id,title,user_id) values ('t20-' || gen_random_uuid()::text,'t',%(u)s)",
}


# scribe_app is rejected either way: PostgreSQL evaluates the RLS WITH CHECK (workspace_id = <context>, NULL is never equal)
# before the NOT NULL constraint, so on PG17 the error is InsufficientPrivilege, as test_rls.test_null_workspace_id_is_rejected
# also allows. What matters is that the shim did NOT fill in a workspace (no personal workspace is left for the user).
REJECTED = (psycopg.errors.NotNullViolation, psycopg.errors.InsufficientPrivilege)


def _no_personal_workspace(user):
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        assert personal_ids(c, user) == []


@pytest.mark.parametrize("table", ["files", "chats", "rag_traces"])
def test_scribe_app_with_context_still_cannot_insert_a_null_workspace_id(table):
    user = new_user()
    with fresh_app_connection(uuid.uuid4()) as c:  # explicit NULL
        with pytest.raises(REJECTED):
            c.execute(INSERT_NULL_WS[table], {"u": user})
    if table in LEGACY_SHAPED:
        with fresh_app_connection(uuid.uuid4()) as c:  # workspace_id omitted entirely
            with pytest.raises(REJECTED):
                c.execute(LEGACY_SHAPED[table], {"u": user})
    _no_personal_workspace(user)


def test_scribe_app_with_context_cannot_use_the_shim_to_reach_a_workspace_it_does_not_hold(conn):
    """Context = workspace A, row's user_id = B (no personal workspace yet): the shim stays out, nothing is created for B."""
    a, b = new_user(), new_user()
    wa = conn.execute("select app_ensure_personal_workspace(%s)", (a,)).fetchone()[0]
    as_app(conn, wa)
    with pytest.raises(REJECTED):
        with conn.transaction():
            conn.execute(LEGACY_SHAPED["files"], {"u": b})
    conn.execute("reset session authorization")
    assert personal_ids(conn, b) == []


@pytest.mark.parametrize("table", ["files", "chats", "rag_traces"])
def test_owner_with_context_set_still_gets_not_null_violation(conn, table):
    user = new_user()
    conn.execute("select set_config('app.workspace_id', %s, true)", (str(uuid.uuid4()),))
    with pytest.raises(psycopg.errors.NotNullViolation) as e:
        with conn.transaction():
            conn.execute(INSERT_NULL_WS[table], {"u": user})
    assert e.value.diag.column_name == "workspace_id"
    assert personal_ids(conn, user) == []


def test_shim_is_not_poisoned_by_a_previous_scoped_transaction_on_the_same_connection():
    """Pooled connections (Neon pgbouncer) reuse a backend: after a transaction that did set_config('app.workspace_id', .., true)
    the GUC reads '' (not NULL) and the legacy build must work again."""
    user = new_user()
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True, prepare_threshold=None) as c:
        with c.transaction():
            c.execute("select set_config('app.workspace_id', %s, true)", (str(uuid.uuid4()),))
        assert c.execute("select current_setting('app.workspace_id', true)").fetchone()[0] == ""
        with c.transaction():
            fid, wid = legacy_file(c, user)
            assert wid is not None and wid == the_personal_workspace(c, user)
            raise psycopg.Rollback()


# --- e. RLS still holds ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("table", ["files", "chats"])
def test_scribe_app_without_context_cannot_insert_a_legacy_shaped_row(table):
    user = new_user()
    with fresh_app_connection(None) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(LEGACY_SHAPED[table], {"u": user})
    with fresh_app_connection("") as c:  # empty context is "no context" too
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(LEGACY_SHAPED[table], {"u": user})
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:  # the rolled-back statement left no workspace behind
        assert personal_ids(c, user) == []


def test_scribe_app_without_context_cannot_insert_a_legacy_shaped_rag_trace(conn):
    user = new_user()
    cid = legacy_chat(conn, user)
    wid = the_personal_workspace(conn, user)
    as_app(conn, None)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with conn.transaction():
            legacy_trace(conn, cid, user)
    conn.execute("reset session authorization")
    assert conn.execute("select count(*) from rag_traces where chat_id=%s", (cid,)).fetchone()[0] == 0
    assert personal_ids(conn, user) == [wid]


def test_scribe_app_with_context_a_cannot_see_rows_the_shim_put_in_user_bs_workspace(conn):
    a, b = new_user(), new_user()
    wa = conn.execute("select app_ensure_personal_workspace(%s)", (a,)).fetchone()[0]
    fid, wb = legacy_file(conn, b)
    cid = legacy_chat(conn, b)
    tid = legacy_trace(conn, cid, b)
    assert wa != wb
    as_app(conn, wa)
    assert conn.execute("select count(*) from files where id=%s or workspace_id=%s", (fid, wb)).fetchone()[0] == 0
    assert conn.execute("select count(*) from chats where id=%s", (cid,)).fetchone()[0] == 0
    assert conn.execute("select count(*) from rag_traces where id=%s", (tid,)).fetchone()[0] == 0
    assert conn.execute("select count(*) from workspaces where id=%s", (wb,)).fetchone()[0] == 0
    conn.execute("reset session authorization")
    as_app(conn, wb)  # ... while B's own context sees them
    assert conn.execute("select count(*) from files where id=%s", (fid,)).fetchone()[0] == 1
    assert conn.execute("select count(*) from chats where id=%s", (cid,)).fetchone()[0] == 1
    assert conn.execute("select count(*) from rag_traces where id=%s", (tid,)).fetchone()[0] == 1


# --- f. 0019 composite FK still holds -------------------------------------------------------------------------------
def test_legacy_trace_cannot_point_at_a_chat_in_another_workspace(conn):
    user = new_user()
    ev = conn.execute("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id", (f"t20-eval-{uuid.uuid4()}",)).fetchone()[0]
    foreign_chat = f"t20-chat-{uuid.uuid4()}"
    conn.execute("insert into chats(id,title,user_id,workspace_id) values (%s,'t',%s,%s)", (foreign_chat, user, ev))
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
        with conn.transaction():
            legacy_trace(conn, foreign_chat, user)
    assert "rag_traces_chat_id_ws_fk" in str(e.value)
    own_chat = legacy_chat(conn, user)  # U's own 1.0 chat works
    tid = legacy_trace(conn, own_chat, user)
    assert workspace_of(conn, "rag_traces", "id", tid) == the_personal_workspace(conn, user)


# --- g. null / empty user_id -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("table", ["files", "chats"])
def test_null_user_id_is_still_a_not_null_violation_and_creates_no_workspace(conn, table):
    before = conn.execute("select count(*) from workspaces").fetchone()[0]
    sql = {"files": "insert into files(name,type,size,user_id) values ('x','text/plain',1,NULL)",
           "chats": "insert into chats(id,title,user_id) values ('t20-' || gen_random_uuid()::text,'t',NULL)"}[table]
    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.transaction():
            conn.execute(sql)
    assert conn.execute("select count(*) from workspaces").fetchone()[0] == before


@pytest.mark.parametrize("table", ["files", "chats"])
def test_empty_user_id_leaves_workspace_id_null_so_the_insert_fails(conn, table):
    before = conn.execute("select count(*) from workspaces").fetchone()[0]
    sql = {"files": "insert into files(name,type,size,user_id) values ('x','text/plain',1,'')",
           "chats": "insert into chats(id,title,user_id) values ('t20-' || gen_random_uuid()::text,'t','')"}[table]
    with pytest.raises(psycopg.errors.NotNullViolation) as e:
        with conn.transaction():
            conn.execute(sql)
    assert e.value.diag.column_name == "workspace_id"
    assert conn.execute("select count(*) from workspaces").fetchone()[0] == before


def test_null_user_id_rag_trace_is_not_null_violation_and_creates_no_workspace(conn):
    user = new_user()
    cid = legacy_chat(conn, user)
    before = conn.execute("select count(*) from workspaces").fetchone()[0]
    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.transaction():
            conn.execute("insert into rag_traces(id,chat_id,user_id,query,top_k,threshold) values ('t20-x',%s,NULL,'q',3,0.1)", (cid,))
    assert conn.execute("select count(*) from workspaces").fetchone()[0] == before


# --- h. function and trigger catalog ---------------------------------------------------------------------------------
def test_function_is_security_definer_owned_by_scribe_definer_with_pinned_search_path(conn):
    prosecdef, owner, config, acl = conn.execute(
        "select prosecdef, pg_get_userbyid(proowner), proconfig, proacl::text from pg_proc "
        "where pronamespace='public'::regnamespace and proname='app_legacy_default_workspace'").fetchone()
    assert prosecdef is True and owner == "scribe_definer"
    assert config == ["search_path=pg_catalog, public"]
    assert conn.execute("select count(*) from pg_proc p, aclexplode(p.proacl) a where p.proname='app_legacy_default_workspace' and a.grantee = 0").fetchone()[0] == 0, acl
    assert conn.execute("select has_function_privilege('scribe_app', 'public.app_legacy_default_workspace()', 'execute')").fetchone()[0] is False
    assert conn.execute("select has_function_privilege('public', 'public.app_legacy_default_workspace()', 'execute')").fetchone()[0] is False


def test_three_triggers_present_enabled_before_insert_when_workspace_id_is_null(conn):
    rows = conn.execute(
        "select t.tgname, t.tgrelid::regclass::text, t.tgenabled, (t.tgtype & 2) <> 0 as is_before, (t.tgtype & 4) <> 0 as is_insert, "
        "(t.tgtype & 1) <> 0 as per_row, pg_get_triggerdef(t.oid) from pg_trigger t where not t.tgisinternal and t.tgname = any(%s) order by 1",
        (list(TRIGGERS),)).fetchall()
    assert [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows] == [
        ("chats_legacy_default_workspace", "chats", "O", True, True, True),
        ("files_legacy_default_workspace", "files", "O", True, True, True),
        ("rag_traces_legacy_default_workspace", "rag_traces", "O", True, True, True)]
    for r in rows:
        assert "WHEN (new.workspace_id IS NULL)" in r[6].replace("WHEN ((new.workspace_id IS NULL))", "WHEN (new.workspace_id IS NULL)"), r[6]
        assert "app_legacy_default_workspace()" in r[6]


# --- i. idempotent re-run and rollback ---------------------------------------------------------------------------------
def trigger_count(c) -> int:
    return c.execute("select count(*) from pg_trigger where not tgisinternal and tgname = any(%s)", (list(TRIGGERS),)).fetchone()[0]


def function_exists(c) -> bool:
    return c.execute("select to_regprocedure('public.app_legacy_default_workspace()') is not null").fetchone()[0]


def test_rerunning_the_migration_leaves_exactly_three_triggers(conn):
    sql = MIGRATION.read_text().replace("--> statement-breakpoint", "")
    for _ in range(2):
        conn.execute(sql)
        assert trigger_count(conn) == 3
    assert conn.execute("select count(*) from pg_trigger where not tgisinternal and tgfoid = 'public.app_legacy_default_workspace()'::regprocedure").fetchone()[0] == 3
    assert conn.execute("select pg_get_userbyid(proowner) from pg_proc where proname='app_legacy_default_workspace'").fetchone()[0] == "scribe_definer"
    user = new_user()  # and the shim still works afterwards
    assert legacy_file(conn, user)[1] == the_personal_workspace(conn, user)


def test_rollback_file_removes_triggers_and_function_and_is_transactional(conn):
    down = "\n".join(line for line in ROLLBACK.read_text().splitlines() if line.strip() not in ("BEGIN;", "COMMIT;"))
    assert trigger_count(conn) == 3 and function_exists(conn)
    conn.execute(down)
    assert trigger_count(conn) == 0 and not function_exists(conn)
    # with the shim gone the 1.0 shape fails again (this is what the rollback file means for the legacy build)
    with pytest.raises(psycopg.errors.NotNullViolation):
        with conn.transaction():
            legacy_file(conn, new_user())
    conn.execute(down)  # idempotent (IF EXISTS)
    conn.rollback()
    assert trigger_count(conn) == 3 and function_exists(conn)


def test_rollback_file_also_deletes_the_journal_row_when_the_journal_table_exists(conn):
    down = "\n".join(line for line in ROLLBACK.read_text().splitlines() if line.strip() not in ("BEGIN;", "COMMIT;"))
    conn.execute("create schema if not exists drizzle")
    conn.execute("create table if not exists drizzle.__drizzle_migrations (id serial primary key, hash text not null, created_at bigint)")
    conn.execute("insert into drizzle.__drizzle_migrations(hash, created_at) values ('h20', 1784937600000), ('h19', 1784851200000)")
    conn.execute(down)
    assert conn.execute("select hash from drizzle.__drizzle_migrations where created_at in (1784937600000, 1784851200000)").fetchall() == [("h19",)]


def test_journal_has_the_0020_entry():
    journal = json.loads((DRIZZLE / "meta" / "_journal.json").read_text())
    last = journal["entries"][-1]
    assert (last["idx"], last["tag"], last["when"]) == (20, "0020_legacy_workspace_default", 1784937600000)
    assert last["when"] > journal["entries"][-2]["when"]
    assert (DRIZZLE / f"{last['tag']}.sql").exists() and (DRIZZLE / "rollback" / f"{last['tag']}.down.sql").exists()


# --- j. concurrency ---------------------------------------------------------------------------------------------------
def _connect_for_race() -> psycopg.Connection:
    c = psycopg.connect(DATABASE_URL_TEST, prepare_threshold=None)
    c.execute("set statement_timeout = '20s'")
    return c


def _committed_workspace_ids(user) -> list:
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        return personal_ids(c, user)


def test_concurrent_first_inserts_for_a_new_user_share_one_workspace_while_the_first_is_uncommitted():
    """B's insert blocks on A's uncommitted workspace row (unique personal-owner index), then adopts it once A commits."""
    user = new_user()
    a, b = _connect_for_race(), _connect_for_race()
    result: dict = {}
    try:
        fid_a, wid_a = legacy_file(a, user)  # A creates the workspace, uncommitted

        def run_b():
            try:
                result["b"] = legacy_file(b, user)
                b.commit()
            except Exception as e:  # noqa: BLE001 - surfaced by the assertions below
                result["err"] = e

        t = threading.Thread(target=run_b)
        t.start()
        time.sleep(0.7)
        assert t.is_alive(), "B should be waiting for A's uncommitted workspace"
        a.commit()
        t.join(timeout=25)
        assert not t.is_alive() and "err" not in result, result
        fid_b, wid_b = result["b"]
        assert wid_a == wid_b
        assert _committed_workspace_ids(user) == [wid_a]
        with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
            assert the_personal_workspace(c, user) == wid_a
            assert c.execute("select count(*) from files where id = any(%s) and workspace_id=%s", ([fid_a, fid_b], wid_a)).fetchone()[0] == 2
    finally:
        a.close()
        b.close()
        purge_workspaces(DATABASE_URL_TEST, _committed_workspace_ids(user))


def test_simultaneous_first_inserts_for_a_new_user_share_one_workspace():
    user = new_user()
    n = 4
    barrier = threading.Barrier(n)
    results: list = []
    errors: list = []

    def worker():
        c = _connect_for_race()
        try:
            barrier.wait(timeout=10)
            results.append(legacy_file(c, user))
            c.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors
        assert len(results) == n and len({r[1] for r in results}) == 1
        wids = _committed_workspace_ids(user)
        assert wids == [results[0][1]]
        with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
            assert c.execute("select count(*) from workspace_members where workspace_id=%s and user_id=%s and role='owner'", (wids[0], user)).fetchone()[0] == 1
    finally:
        purge_workspaces(DATABASE_URL_TEST, _committed_workspace_ids(user))
