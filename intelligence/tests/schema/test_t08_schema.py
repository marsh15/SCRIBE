"""T08 schema tests: Scribe 2.0 tables, indexes, constraints, triggers, FTS.

Needs a Postgres+pgvector database that has the Drizzle schema AND migrations
0010/0011 applied (0010 holds the immutability triggers, 0011 the seed profiles):

    DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/t08 \\
      scripts/apply-migrations.sh "$DATABASE_URL_TEST" \\
      && psql "$DATABASE_URL_TEST" -f drizzle/0010_source_versions_generations.sql \\
      && psql "$DATABASE_URL_TEST" -f drizzle/0011_seed_profiles.sql

Every data-writing test runs in a transaction that is rolled back.
"""
import uuid

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db

LEGACY_INDEX = "10000000-0000-4000-8000-000000000001"
STRUCTURAL_INDEX = "10000000-0000-4000-8000-000000000002"

NEW_TABLES = [
    "source_versions", "embedding_profiles", "chunk_profiles", "index_profiles",
    "retrieval_profiles", "index_generations", "active_indexes", "document_blocks",
    "chunks", "chunk_source_spans", "chunk_embeddings_1536", "chunk_embeddings_3072",
    "retrieval_traces", "retrieval_candidates", "evidence_bundles", "evidence_items",
    "claim_verifications", "eval_runs", "eval_results",
]
WORKSPACE_OWNED = [t for t in NEW_TABLES if t not in (
    "embedding_profiles", "chunk_profiles", "index_profiles", "retrieval_profiles",
    "eval_runs", "eval_results")]


@pytest.fixture()
def conn():
    with psycopg.connect(DATABASE_URL_TEST, prepare_threshold=None) as c:
        try:
            yield c
        finally:
            c.rollback()


def scalar(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]


def fail_sql(conn, sql, params=None, exc=psycopg.Error):
    """Run sql expecting an error; roll back to a savepoint so the txn stays usable."""
    with pytest.raises(exc) as info:
        with conn.transaction():
            conn.execute(sql, params)
    return info.value


@pytest.fixture()
def seed(conn):
    """workspace + file + source_version + BUILDING generation (legacy profile)."""
    ws = scalar(conn, "insert into workspaces(kind, owner_user_id) values ('personal', %s) returning id",
                (f"t08-{uuid.uuid4()}",))
    file_id = scalar(
        conn,
        "insert into files(name, type, size, user_id, workspace_id) values "
        "('f.txt','text/plain',1,'t08',%s) returning id", (ws,))
    ver = scalar(
        conn,
        "insert into source_versions(source_id, workspace_id, version_number, sha256, upload_state) "
        "values (%s,%s,1,'abc','stored') returning id", (file_id, ws))
    gen = scalar(
        conn,
        "insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id) "
        "values (%s,%s,%s,%s) returning id", (file_id, ver, LEGACY_INDEX, ws))
    return {"ws": ws, "file": file_id, "ver": ver, "gen": gen}


def add_chunk(conn, s, idx, content, heading=None):
    return scalar(
        conn,
        "insert into chunks(generation_id, source_version_id, workspace_id, chunk_index, content, "
        "heading_text, content_hash) values (%s,%s,%s,%s,%s,%s,'h') returning id",
        (s["gen"], s["ver"], s["ws"], idx, content, heading))


# ---------------------------------------------------------------- structure

def test_all_new_tables_exist(conn):
    rows = conn.execute(
        "select table_name from information_schema.tables where table_schema='public'").fetchall()
    have = {r[0] for r in rows}
    assert not set(NEW_TABLES) - have, f"missing: {sorted(set(NEW_TABLES) - have)}"


def test_extended_columns(conn):
    def cols(t):
        return {r[0] for r in conn.execute(
            "select column_name from information_schema.columns where table_schema='public' "
            "and table_name=%s", (t,)).fetchall()}
    assert {"deleted_at", "desired_version_id", "active_version_id"} <= cols("files")
    assert {"source_version_id", "generation_id", "lease_token", "lease_expires_at", "attempts",
            "error_code", "last_error", "next_retry_at"} <= cols("ingestion_jobs")
    assert {"id", "generation_id", "source_version_id", "workspace_id", "parent_id", "chunk_index",
            "content", "heading_text", "content_hash", "token_count", "fts_en", "fts_simple"} <= cols("chunks")
    assert {"heading_path", "bbox", "text", "ocr", "row_number", "char_start", "char_end",
            "meta"} <= cols("document_blocks")


def test_new_ids_are_uuid_with_default(conn):
    rows = conn.execute(
        "select table_name, data_type, column_default from information_schema.columns "
        "where table_schema='public' and column_name='id' and table_name = any(%s)",
        ([t for t in NEW_TABLES if t not in ("active_indexes", "eval_results")],)).fetchall()
    for table, dtype, default in rows:
        assert dtype == "uuid", table
        assert "gen_random_uuid" in (default or ""), table


def test_vector_dimensions(conn):
    for table, dims in (("chunk_embeddings_1536", 1536), ("chunk_embeddings_3072", 3072)):
        typ = scalar(conn, "select format_type(atttypid, atttypmod) from pg_attribute "
                           "where attrelid=%s::regclass and attname='embedding'", (table,))
        assert typ == f"vector({dims})"


def test_generated_columns_are_stored(conn):
    rows = dict(conn.execute(
        "select attname, attgenerated from pg_attribute where attrelid='chunks'::regclass "
        "and attname in ('fts_en','fts_simple')").fetchall())
    assert rows == {"fts_en": "s", "fts_simple": "s"}


def test_hnsw_on_1536_and_no_ann_on_3072(conn):
    defs = {r[0]: r[1] for r in conn.execute(
        "select tablename, indexdef from pg_indexes where schemaname='public' "
        "and tablename in ('chunk_embeddings_1536','chunk_embeddings_3072') "
        "and (indexdef ilike '%using hnsw%' or indexdef ilike '%using ivfflat%')").fetchall()}
    assert "chunk_embeddings_1536" in defs
    d = defs["chunk_embeddings_1536"]
    assert "vector_cosine_ops" in d and "m='16'" in d and "ef_construction='64'" in d
    assert "chunk_embeddings_3072" not in defs


def test_gin_indexes_on_fts(conn):
    defs = [r[0] for r in conn.execute(
        "select indexdef from pg_indexes where schemaname='public' and tablename='chunks' and indexdef ilike '%using gin%'").fetchall()]
    assert any("(fts_en)" in d for d in defs)
    assert any("(fts_simple)" in d for d in defs)


@pytest.mark.parametrize("table", WORKSPACE_OWNED)
def test_workspace_id_present_and_indexed(conn, table):
    assert scalar(conn, "select count(*) from information_schema.columns where table_schema='public' and table_name=%s "
                        "and column_name='workspace_id' and is_nullable='NO'", (table,)) == 1
    idx = [r[0] for r in conn.execute(
        "select indexdef from pg_indexes where schemaname='public' and tablename=%s", (table,)).fetchall()]
    assert any("(workspace_id" in d for d in idx), f"{table} lacks a workspace_id index"


def test_chunks_btree_indexes(conn):
    idx = " ".join(r[0] for r in conn.execute(
        "select indexdef from pg_indexes where schemaname='public' and tablename='chunks'").fetchall())
    for col in ("workspace_id", "generation_id", "source_version_id"):
        assert f"btree ({col})" in idx, col


def test_seed_profiles_present(conn):
    names = lambda t: {r[0] for r in conn.execute(f"select name from {t}").fetchall()}  # noqa: E731
    assert {"legacy-gemini-embedding-2-preview-3072", "gemini-embedding-2-1536"} <= names("embedding_profiles")
    assert {"legacy-char-4000-400", "structural-v1"} <= names("chunk_profiles")
    assert {"legacy-v1", "structural-v1"} <= names("index_profiles")
    cfg = scalar(conn, "select config from chunk_profiles where name='structural-v1'")
    assert cfg["child_max_tokens"] == 384 and cfg["overlap_tokens"] == 48
    assert cfg["parent_target_tokens"] == 1536 and cfg["tokenizer"] == "o200k_base"


# -------------------------------------------------------------- constraints

def test_generation_status_check(conn, seed):
    fail_sql(conn, "update index_generations set status='BOGUS' where id=%s", (seed["gen"],),
             psycopg.errors.CheckViolation)
    for status in ("READY", "FAILED", "CANCELLED", "SUPERSEDED", "BUILDING"):
        conn.execute("update index_generations set status=%s where id=%s", (status, seed["gen"]))


def test_unique_version_number_and_block_index(conn, seed):
    fail_sql(conn, "insert into source_versions(source_id, workspace_id, version_number) values (%s,%s,1)",
             (seed["file"], seed["ws"]), psycopg.errors.UniqueViolation)
    conn.execute("insert into document_blocks(source_version_id, workspace_id, block_index, type, text) "
                 "values (%s,%s,0,'paragraph','x')", (seed["ver"], seed["ws"]))
    fail_sql(conn, "insert into document_blocks(source_version_id, workspace_id, block_index, type, text) "
                   "values (%s,%s,0,'paragraph','y')", (seed["ver"], seed["ws"]),
             psycopg.errors.UniqueViolation)


def test_bbox_must_have_four_values(conn, seed):
    conn.execute("insert into document_blocks(source_version_id, workspace_id, block_index, type, text, bbox)"
                 " values (%s,%s,1,'p','x','{1,2,3,4}')", (seed["ver"], seed["ws"]))
    fail_sql(conn, "insert into document_blocks(source_version_id, workspace_id, block_index, type, text, bbox)"
                   " values (%s,%s,2,'p','x','{1,2,3}')", (seed["ver"], seed["ws"]),
             psycopg.errors.CheckViolation)


def test_one_active_generation_per_source_and_profile(conn, seed):
    conn.execute("update index_generations set status='ACTIVE' where id=%s", (seed["gen"],))
    ver2 = scalar(conn, "insert into source_versions(source_id, workspace_id, version_number) "
                        "values (%s,%s,2) returning id", (seed["file"], seed["ws"]))
    gen2 = scalar(conn, "insert into index_generations(source_id, source_version_id, index_profile_id, "
                        "workspace_id) values (%s,%s,%s,%s) returning id",
                  (seed["file"], ver2, LEGACY_INDEX, seed["ws"]))
    fail_sql(conn, "update index_generations set status='ACTIVE' where id=%s", (gen2,),
             psycopg.errors.UniqueViolation)
    # A different index profile may be active concurrently for the same source.
    gen3 = scalar(conn, "insert into index_generations(source_id, source_version_id, index_profile_id, "
                        "workspace_id, status) values (%s,%s,%s,%s,'ACTIVE') returning id",
                  (seed["file"], seed["ver"], STRUCTURAL_INDEX, seed["ws"]))
    assert gen3
    # Switching in one transaction (supersede old first) works.
    conn.execute("update index_generations set status='SUPERSEDED' where id=%s", (seed["gen"],))
    conn.execute("update index_generations set status='ACTIVE' where id=%s", (gen2,))


def test_generation_requires_matching_source(conn, seed):
    other = scalar(conn, "insert into files(name,type,size,user_id,workspace_id) values "
                         "('o','text/plain',1,'t08',%s) returning id", (seed["ws"],))
    fail_sql(conn, "insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id)"
                   " values (%s,%s,%s,%s)", (other, seed["ver"], STRUCTURAL_INDEX, seed["ws"]),
             psycopg.errors.ForeignKeyViolation)


def test_active_indexes_pk_and_pointer_consistency(conn, seed):
    conn.execute("update index_generations set status='ACTIVE' where id=%s", (seed["gen"],))
    conn.execute("insert into active_indexes(source_id, index_profile_id, generation_id, workspace_id) "
                 "values (%s,%s,%s,%s)", (seed["file"], LEGACY_INDEX, seed["gen"], seed["ws"]))
    fail_sql(conn, "insert into active_indexes(source_id, index_profile_id, generation_id, workspace_id) "
                   "values (%s,%s,%s,%s)", (seed["file"], LEGACY_INDEX, seed["gen"], seed["ws"]),
             psycopg.errors.UniqueViolation)
    pk = scalar(conn, "select pg_get_constraintdef(oid) from pg_constraint where conrelid='active_indexes'::regclass "
                      "and contype='p'")
    assert pk == "PRIMARY KEY (source_id, index_profile_id)"
    # Pointer to a generation of a different profile is rejected by the composite FK.
    fail_sql(conn, "insert into active_indexes(source_id, index_profile_id, generation_id, workspace_id) "
                   "values (%s,%s,%s,%s)", (seed["file"], STRUCTURAL_INDEX, seed["gen"], seed["ws"]),
             psycopg.errors.ForeignKeyViolation)


def test_chunk_parent_self_reference(conn, seed):
    parent = add_chunk(conn, seed, 0, "parent")
    child = scalar(conn, "insert into chunks(generation_id, source_version_id, workspace_id, parent_id, "
                         "chunk_index, content, content_hash) values (%s,%s,%s,%s,1,'c','h') returning id",
                   (seed["gen"], seed["ver"], seed["ws"], parent))
    assert scalar(conn, "select parent_id from chunks where id=%s", (child,)) == parent
    fail_sql(conn, "insert into chunks(generation_id, source_version_id, workspace_id, chunk_index, content, "
                   "content_hash) values (%s,%s,%s,0,'dup','h')", (seed["gen"], seed["ver"], seed["ws"]),
             psycopg.errors.UniqueViolation)


# ------------------------------------------------------------ immutability

@pytest.mark.parametrize("table,col", [
    ("embedding_profiles", "name"), ("chunk_profiles", "name"), ("index_profiles", "name"),
])
def test_seed_profiles_are_immutable(conn, table, col):
    err = fail_sql(conn, f"update {table} set {col} = {col} || 'x'", exc=psycopg.errors.RestrictViolation)
    assert "immutable" in str(err)


def test_retrieval_profiles_immutable(conn):
    pid = scalar(conn, "insert into retrieval_profiles(name, config, config_hash) values (%s,'{}','h') returning id",
                 (f"t08-{uuid.uuid4()}",))
    fail_sql(conn, "update retrieval_profiles set config='{\"a\":1}' where id=%s", (pid,),
             psycopg.errors.RestrictViolation)


def test_source_version_frozen_except_duplicate_of_and_upload_state(conn, seed):
    for col, val in (("sha256", "'zzz'"), ("storage_key", "'k'"), ("size_bytes", "5"),
                     ("mime_type", "'x/y'"), ("version_number", "9"), ("created_by", "'z'")):
        err = fail_sql(conn, f"update source_versions set {col} = {val} where id=%s", (seed["ver"],),
                       psycopg.errors.RestrictViolation)
        assert "immutable" in str(err)
    ver2 = scalar(conn, "insert into source_versions(source_id, workspace_id, version_number) "
                        "values (%s,%s,2) returning id", (seed["file"], seed["ws"]))
    conn.execute("update source_versions set duplicate_of=%s, upload_state='failed' where id=%s",
                 (seed["ver"], ver2))
    assert scalar(conn, "select upload_state from source_versions where id=%s", (ver2,)) == "failed"
    fail_sql(conn, "update source_versions set upload_state='nope' where id=%s", (ver2,),
             psycopg.errors.CheckViolation)


# ----------------------------------------------------------------------- FTS

def test_fts_simple_matches_identifier_exactly(conn, seed):
    hit = add_chunk(conn, seed, 0, "Request failed with ERR_AUTH_403 for the service account")
    miss = add_chunk(conn, seed, 1, "Request failed with ERR_AUTH_500 for the service account")
    ids = {r[0] for r in conn.execute(
        "select id from chunks where generation_id=%s and fts_simple @@ websearch_to_tsquery('simple', %s)",
        (seed["gen"], "ERR_AUTH_403")).fetchall()}
    assert ids == {hit}
    assert miss not in ids


def test_fts_en_stems_and_weights_heading(conn, seed):
    c = add_chunk(conn, seed, 0, "Users are authenticating against the gateway", heading="Login flow")
    q = "select count(*) from chunks where id=%s and fts_en @@ websearch_to_tsquery('english', %s)"
    assert scalar(conn, q, (c, "authenticate")) == 1       # stemmed word matches
    assert scalar(conn, q, (c, "login")) == 1              # heading text is indexed
    # no stemming in fts_simple: the stem does not match the inflected form
    assert scalar(conn, "select count(*) from chunks where id=%s and fts_simple @@ "
                        "websearch_to_tsquery('simple','authenticate')", (c,)) == 0
    weights = scalar(conn, "select fts_en::text from chunks where id=%s", (c,))
    assert "'login':" in weights and "A" in weights        # heading lexemes carry weight A


def test_generated_columns_update_with_content(conn, seed):
    c = add_chunk(conn, seed, 0, "alpha")
    conn.execute("update chunks set content='bravo' where id=%s", (c,))
    assert scalar(conn, "select fts_simple @@ to_tsquery('simple','bravo') from chunks where id=%s", (c,))


def test_embeddings_roundtrip_and_cascade(conn, seed):
    c = add_chunk(conn, seed, 0, "vec")
    prof = scalar(conn, "select embedding_profile_id from index_profiles where id=%s", (STRUCTURAL_INDEX,))
    vec = "[" + ",".join(["0.01"] * 1536) + "]"
    conn.execute("insert into chunk_embeddings_1536(chunk_id, workspace_id, embedding_profile_id, embedding) "
                 "values (%s,%s,%s,%s)", (c, seed["ws"], prof, vec))
    d = scalar(conn, "select embedding <=> %s::vector from chunk_embeddings_1536 where chunk_id=%s", (vec, c))
    assert abs(d) < 1e-6
    conn.execute("delete from chunks where id=%s", (c,))
    assert scalar(conn, "select count(*) from chunk_embeddings_1536 where chunk_id=%s", (c,)) == 0
