"""Migration 0019: composite (id, workspace_id) FKs reject cross-workspace references, and the migration's pre-check
aborts with a helpful message when existing data already violates them. Every test rolls back."""
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db

MIGRATION = Path(__file__).resolve().parents[3] / "drizzle" / "0019_workspace_composite_fks.sql"
INDEX_PROFILE = "10000000-0000-4000-8000-000000000001"


@pytest.fixture()
def conn():
    with psycopg.connect(DATABASE_URL_TEST, prepare_threshold=None) as c:
        try:
            yield c
        finally:
            c.rollback()


class World:
    """Two workspaces, each with a file, a source version, a generation and a chat."""

    def __init__(self, c):
        self.c = c
        self.ws = {k: self._ws(k) for k in "ab"}
        self.file = {k: self._file(k) for k in "ab"}
        self.sv = {k: self._sv(k) for k in "ab"}
        self.gen = {k: self._gen(k) for k in "ab"}
        self.chat = {k: self._chat(k) for k in "ab"}

    def one(self, sql, params=()):
        return self.c.execute(sql, params).fetchone()[0]

    def _ws(self, k):
        return self.one("insert into workspaces(kind, owner_user_id) values ('eval', %s) returning id", (f"t19-{k}-{uuid.uuid4()}",))

    def _file(self, k):
        return self.one("insert into files(name,type,size,user_id,workspace_id,status) values ('f','text/plain',1,'t19',%s,'ready') returning id", (self.ws[k],))

    def _sv(self, k):
        return self.one("insert into source_versions(source_id, workspace_id, version_number) values (%s,%s,1) returning id", (self.file[k], self.ws[k]))

    def _gen(self, k):
        return self.one("insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id) values (%s,%s,%s,%s) returning id",
                        (self.file[k], self.sv[k], INDEX_PROFILE, self.ws[k]))

    def _chat(self, k):
        cid = f"t19-{uuid.uuid4()}"
        self.c.execute("insert into chats(id, title, user_id, workspace_id) values (%s,'t','t19',%s)", (cid, self.ws[k]))
        return cid

    def block(self, ws, sv):
        return self.c.execute("insert into document_blocks(source_version_id, workspace_id, block_index, type, text) values (%s,%s,0,'paragraph','x')", (sv, ws))

    def chunk(self, ws, gen, sv):
        return self.c.execute("insert into chunks(generation_id, source_version_id, workspace_id, chunk_index, content, content_hash) values (%s,%s,%s,0,'x','h')", (gen, sv, ws))


@pytest.fixture()
def w(conn):
    return World(conn)


def rejected(conn, fn, constraint):
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as e:
        with conn.transaction():
            fn()
    assert constraint in str(e.value), str(e.value)


def test_same_workspace_references_still_work(w):
    w.block(w.ws["a"], w.sv["a"])
    w.chunk(w.ws["a"], w.gen["a"], w.sv["a"])


def test_source_version_cannot_point_at_other_workspaces_file(w, conn):
    rejected(conn, lambda: conn.execute("insert into source_versions(source_id, workspace_id, version_number) values (%s,%s,2)", (w.file["a"], w.ws["b"])),
             "source_versions_source_id_ws_fk")


def test_document_block_cannot_point_at_other_workspaces_version(w, conn):
    rejected(conn, lambda: w.block(w.ws["b"], w.sv["a"]), "document_blocks_source_version_id_ws_fk")


def test_chunk_cannot_point_at_other_workspaces_generation_or_version(w, conn):
    rejected(conn, lambda: w.chunk(w.ws["b"], w.gen["a"], w.sv["b"]), "chunks_generation_id_ws_fk")
    rejected(conn, lambda: w.chunk(w.ws["b"], w.gen["b"], w.sv["a"]), "chunks_source_version_id_ws_fk")


def test_generation_cannot_point_at_other_workspaces_file(w, conn):
    rejected(conn, lambda: conn.execute(
        "insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id) values (%s,%s,%s,%s)",
        (w.file["a"], w.sv["a"], "10000000-0000-4000-8000-000000000002", w.ws["b"])), "index_generations_source_id_ws_fk")


def test_file_active_version_cannot_be_another_workspaces(w, conn):
    rejected(conn, lambda: conn.execute("update files set active_version_id=%s where id=%s", (w.sv["b"], w.file["a"])),
             "files_active_version_id_ws_fk")


def test_trace_and_rag_trace_cannot_point_at_other_workspaces_chat(w, conn):
    rejected(conn, lambda: conn.execute("insert into retrieval_traces(workspace_id, query, chat_id) values (%s,'q',%s)", (w.ws["a"], w.chat["b"])),
             "retrieval_traces_chat_id_ws_fk")
    rejected(conn, lambda: conn.execute(
        "insert into rag_traces(id, chat_id, user_id, workspace_id, query, top_k, threshold) values (%s,%s,'t19',%s,'q',3,0.1)",
        (f"t19-{uuid.uuid4()}", w.chat["b"], w.ws["a"])), "rag_traces_chat_id_ws_fk")


def test_evidence_bundle_cannot_point_at_other_workspaces_trace(w, conn):
    tid = w.one("insert into retrieval_traces(workspace_id, query) values (%s,'q') returning id", (w.ws["a"],))
    rejected(conn, lambda: conn.execute("insert into evidence_bundles(trace_id, workspace_id, answerability) values (%s,%s,'answerable')", (tid, w.ws["b"])),
             "evidence_bundles_trace_id_ws_fk")


def test_delete_actions_keep_working_and_set_null_spares_workspace_id(w, conn):
    # SET NULL must null only the reference column, never workspace_id (NOT NULL).
    conn.execute("update files set active_version_id=%s where id=%s", (w.sv["a"], w.file["a"]))
    conn.execute("update files set active_version_id=null where id=%s", (w.file["a"],))  # drop the pointer first (index_generations hold the version)
    conn.execute("update files set desired_version_id=%s where id=%s", (w.sv["a"], w.file["a"]))
    conn.execute("delete from index_generations where id=%s", (w.gen["a"],))
    conn.execute("delete from source_versions where id=%s", (w.sv["a"],))
    row = conn.execute("select desired_version_id, workspace_id from files where id=%s", (w.file["a"],)).fetchone()
    assert row == (None, w.ws["a"])
    # cascade: deleting the file removes its remaining children
    conn.execute("delete from files where id=%s", (w.file["a"],))
    assert conn.execute("select count(*) from source_versions where workspace_id=%s", (w.ws["a"],)).fetchone()[0] == 0


def test_precheck_aborts_with_helpful_message_on_existing_violation(w, conn):
    # Simulate legacy bad data: remove the composite FK, insert a cross-workspace row, replay the migration.
    conn.execute("alter table document_blocks drop constraint document_blocks_source_version_id_ws_fk")
    w.block(w.ws["b"], w.sv["a"])
    statements = [s for s in MIGRATION.read_text().split("--> statement-breakpoint") if s.strip()]
    with pytest.raises(psycopg.errors.RaiseException) as e:
        with conn.transaction():
            for s in statements:
                conn.execute(s)
    msg = str(e.value)
    assert "0019 aborted" in msg and "document_blocks.source_version_id -> source_versions: 1 row(s)" in msg


def test_migration_is_idempotent(conn):
    for s in [s for s in MIGRATION.read_text().split("--> statement-breakpoint") if s.strip()]:
        conn.execute(s)
