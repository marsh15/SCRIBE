-- Read-only facts the migrations depend on. Run it on the Neon rehearsal branch BEFORE apply.sh, and again unchanged on
-- production right before cutover (it writes nothing: everything runs in a READ ONLY transaction that is rolled back).
--   psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/precheck.sql | tee rehearsal/precheck.txt
\pset pager off
\set ON_ERROR_STOP 1
BEGIN READ ONLY;

\echo '== 1. server and connection role'
SELECT current_setting('server_version') AS server_version, current_setting('server_version_num')::int AS version_num,
       current_user, current_database();
SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit FROM pg_roles WHERE rolname = current_user;
SELECT r.rolname AS member_of FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid
WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user) ORDER BY 1;
\echo '   NEED version_num >= 150000 (0019 composite FKs with ON DELETE SET NULL (col)) and rolcreaterole = t (0013 creates roles).'
\echo '   rolbypassrls = t means the 0013 RLS policies do not apply to this connection (HANDOVER section 11, item 3).'

\echo '== 2. pgvector'
SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';
\echo '   NEED installed_version set (0010 aborts without it). >= 0.8.0 for iterative index scans, else keep dense_mode=exact.'

\echo '== 3. drizzle migration journal'
SELECT to_regclass('drizzle.__drizzle_migrations') IS NOT NULL AS has_journal \gset
\if :has_journal
  SELECT id, hash, created_at, to_timestamp(created_at / 1000.0) AS created_at_ts
  FROM drizzle.__drizzle_migrations ORDER BY created_at;
  \echo '   EXPECT the last created_at = 1783209600000 (0007_rag_observability). A larger value means a 2.0 migration already ran.'
\else
  \echo '   No drizzle.__drizzle_migrations: this schema was not built by drizzle-kit migrate (expected if it was pushed).'
\endif

\echo '== 4. Scribe 2.0 objects already present (EXPECT no rows in each of the three queries)'
SELECT t AS already_present FROM unnest(ARRAY['workspaces','workspace_members','source_versions','index_generations',
  'active_indexes','chunks','chunk_embeddings_1536','chunk_embeddings_3072','retrieval_profiles']) AS t
WHERE to_regclass('public.' || t) IS NOT NULL;
SELECT rolname AS role_already_present FROM pg_roles WHERE rolname IN ('scribe_app', 'scribe_definer');
SELECT table_name AS has_workspace_id FROM information_schema.columns
WHERE table_schema = 'public' AND column_name = 'workspace_id' ORDER BY 1;

\echo '== 5. rows the migrations touch'
SELECT 'files' AS tbl, count(*) AS row_count, count(*) FILTER (WHERE user_id IS NULL) AS null_user_id FROM files
UNION ALL SELECT 'chats', count(*), count(*) FILTER (WHERE user_id IS NULL) FROM chats
UNION ALL SELECT 'rag_traces', count(*), count(*) FILTER (WHERE user_id IS NULL) FROM rag_traces;
\echo '   NEED null_user_id = 0 (0008 derives workspace_id from user_id and then sets it NOT NULL).'
SELECT count(DISTINCT user_id) AS personal_workspaces_0008_will_create FROM (
  SELECT user_id FROM files UNION SELECT user_id FROM chats UNION SELECT user_id FROM rag_traces) AS u
WHERE user_id IS NOT NULL;
SELECT status AS file_status, count(*) FROM files GROUP BY 1 ORDER BY 1;
SELECT count(*) AS files_0009_will_mark_failed FROM files
WHERE status IN ('uploading', 'queued', 'processing', 'retrying') AND storage_url IS NULL AND storage_key IS NULL;
SELECT status AS job_status, count(*) FROM ingestion_jobs GROUP BY 1 ORDER BY 1;
SELECT count(*) AS documents, count(DISTINCT file_id) AS files_with_documents FROM documents;

\echo '== 6. sizes'
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;
SELECT relname, pg_size_pretty(pg_total_relation_size(oid)) AS total_size FROM pg_class
WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY pg_total_relation_size(oid) DESC LIMIT 10;

ROLLBACK;
