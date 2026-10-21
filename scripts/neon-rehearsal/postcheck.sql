-- Read-only checks after apply.sh (and again after the legacy backfill). Every "EXPECT" line says what passing looks like.
--   psql "$BRANCH_URL" -X -f scripts/neon-rehearsal/postcheck.sql | tee rehearsal/postcheck.txt
\pset pager off
\set ON_ERROR_STOP 1
BEGIN READ ONLY;

\echo '== 1. tables (EXPECT no rows)'
SELECT t AS missing_table FROM unnest(ARRAY['workspaces','workspace_members','source_versions','chunk_profiles',
  'embedding_profiles','index_profiles','retrieval_profiles','index_generations','active_indexes','document_blocks',
  'chunks','chunk_source_spans','chunk_embeddings_1536','chunk_embeddings_3072','retrieval_traces',
  'retrieval_candidates','evidence_bundles','evidence_items','claim_verifications','eval_runs','eval_results',
  'source_version_ocr_pages']) AS t
WHERE to_regclass('public.' || t) IS NULL;

\echo '== 2. seeds (EXPECT retrieval_profiles = 5 and a legacy-v1 index profile)'
SELECT 'chunk_profiles' AS tbl, count(*) FROM chunk_profiles
UNION ALL SELECT 'embedding_profiles', count(*) FROM embedding_profiles
UNION ALL SELECT 'index_profiles', count(*) FROM index_profiles
UNION ALL SELECT 'retrieval_profiles', count(*) FROM retrieval_profiles;
SELECT name AS index_profile FROM index_profiles ORDER BY 1;

\echo '== 3. workspace backfill (EXPECT personal_workspaces = distinct users, and zero mismatches)'
SELECT (SELECT count(*) FROM workspaces WHERE kind = 'personal') AS personal_workspaces,
       (SELECT count(DISTINCT user_id) FROM (SELECT user_id FROM files UNION SELECT user_id FROM chats
          UNION SELECT user_id FROM rag_traces) AS u) AS distinct_users,
       (SELECT count(*) FROM workspaces w WHERE w.kind = 'personal' AND NOT EXISTS (SELECT 1 FROM workspace_members m
          WHERE m.workspace_id = w.id AND m.user_id = w.owner_user_id AND m.role = 'owner')) AS workspaces_without_owner;
SELECT 'files' AS tbl, count(*) AS owner_mismatch FROM files t JOIN workspaces w ON w.id = t.workspace_id
  WHERE w.owner_user_id IS DISTINCT FROM t.user_id
UNION ALL SELECT 'chats', count(*) FROM chats t JOIN workspaces w ON w.id = t.workspace_id
  WHERE w.owner_user_id IS DISTINCT FROM t.user_id
UNION ALL SELECT 'rag_traces', count(*) FROM rag_traces t JOIN workspaces w ON w.id = t.workspace_id
  WHERE w.owner_user_id IS DISTINCT FROM t.user_id;

\echo '== 4. RLS (EXPECT no rows: every workspace table has RLS on, FORCE off, and at least one policy)'
SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.policyname) AS policies
FROM pg_class c LEFT JOIN pg_policies p ON p.schemaname = 'public' AND p.tablename = c.relname
WHERE c.relnamespace = 'public'::regnamespace AND c.relname = ANY (ARRAY['files','chats','rag_traces','source_versions',
  'index_generations','active_indexes','document_blocks','chunks','chunk_source_spans','chunk_embeddings_1536',
  'chunk_embeddings_3072','retrieval_traces','retrieval_candidates','evidence_bundles','evidence_items',
  'claim_verifications','workspace_members','workspaces','chat_messages','ingestion_jobs','documents',
  'rag_trace_chunks','rag_evaluations'])
GROUP BY 1, 2, 3 HAVING NOT c.relrowsecurity OR c.relforcerowsecurity OR count(p.policyname) = 0;

\echo '== 5. roles and functions (EXPECT both roles NOLOGIN/NOBYPASSRLS; app_* SECURITY DEFINER owned by scribe_definer, scribe_* INVOKER owned by the migrating role)'
SELECT rolname, rolcanlogin, rolbypassrls, rolsuper FROM pg_roles WHERE rolname IN ('scribe_app', 'scribe_definer') ORDER BY 1;
SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p
WHERE p.pronamespace = 'public'::regnamespace AND (p.proname LIKE 'app\_%' OR p.proname LIKE 'scribe\_%') ORDER BY 1;
SELECT has_schema_privilege('scribe_definer', 'public', 'CREATE') AS definer_can_create_in_public;
\echo '   EXPECT definer_can_create_in_public = f (the 0013/0014/0016 preamble lends CREATE and must take it back).'

\echo '== 6. indexes (EXPECT no invalid indexes, both new indexes present, the old unique index gone)'
SELECT indexrelid::regclass AS invalid_index FROM pg_index WHERE NOT indisvalid;
SELECT indexname, indexname <> 'ingestion_jobs_file_id_unique' AS ok FROM pg_indexes WHERE schemaname = 'public'
  AND indexname IN ('chunk_embeddings_1536_hnsw', 'ingestion_jobs_file_version_unique', 'ingestion_jobs_file_id_unique')
ORDER BY 1;

\echo '== 6b. 0020 legacy-build shim (EXPECT 3 rows, all tgenabled = O; the function SECURITY DEFINER owned by scribe_definer)'
SELECT t.tgrelid::regclass AS tbl, t.tgname, t.tgenabled FROM pg_trigger t
WHERE NOT t.tgisinternal AND t.tgname IN ('files_legacy_default_workspace', 'chats_legacy_default_workspace',
  'rag_traces_legacy_default_workspace') ORDER BY 1;
SELECT p.proname, p.prosecdef, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p
WHERE p.pronamespace = 'public'::regnamespace AND p.proname = 'app_legacy_default_workspace';

\echo '== 7. legacy backfill (run after scripts/backfill-legacy-generations.ts; EXPECT 0 in both "without" columns for ready files)'
SELECT f.status AS file_status, count(*) AS files,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM source_versions s WHERE s.source_id = f.id)) AS without_version,
       count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM active_indexes a JOIN index_profiles p ON p.id = a.index_profile_id
         AND p.name = 'legacy-v1' WHERE a.source_id = f.id)) AS without_active_legacy_index
FROM files f WHERE f.deleted_at IS NULL GROUP BY 1 ORDER BY 1;
SELECT (SELECT count(*) FROM documents) AS legacy_documents,
       (SELECT count(*) FROM chunk_embeddings_3072) AS legacy_3072_embeddings;

ROLLBACK;
