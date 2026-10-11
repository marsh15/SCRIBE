-- Rollback for drizzle/0013_rls_workspace_isolation.sql. Not part of the drizzle journal; run manually with psql.
BEGIN;
DROP FUNCTION IF EXISTS public.app_workspace_ids_for_user(text);
DROP FUNCTION IF EXISTS public.app_ensure_personal_workspace(text);
DROP FUNCTION IF EXISTS public.app_claim_ingestion_job(integer, integer);
DROP FUNCTION IF EXISTS public.app_sweep_expired_leases(integer);
DROP FUNCTION IF EXISTS public.app_list_purgeable_sources(integer, integer);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'files','chats','rag_traces','source_versions','index_generations','active_indexes','document_blocks','chunks',
    'chunk_source_spans','chunk_embeddings_1536','chunk_embeddings_3072','retrieval_traces','retrieval_candidates',
    'evidence_bundles','evidence_items','claim_verifications','workspace_members','workspaces',
    'chat_messages','ingestion_jobs','documents','rag_trace_chunks','rag_evaluations']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON public.%I', t);
    EXECUTE format('DROP POLICY IF EXISTS definer_access ON public.%I', t);
    EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;

-- Forget the migration so a re-apply is possible (journal 'when' = created_at).
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784332800000;
  END IF;
END $$;

-- Undo the membership granted so a non-superuser migrator could own the functions (no-op for superusers).
DO $$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    EXECUTE format('REVOKE scribe_definer FROM %I', current_user);
  END IF;
END $$;

-- Roles last. If the DB has other dependents (e.g. login enabled, extra grants) drop them by hand first.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM scribe_app, scribe_definer;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM scribe_app, scribe_definer;
REVOKE ALL ON SCHEMA public FROM scribe_app, scribe_definer;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM scribe_app, scribe_definer;
DROP ROLE IF EXISTS scribe_app;
DROP ROLE IF EXISTS scribe_definer;

COMMIT;
