-- DEFERRED (not journaled, never run by drizzle): FORCE ROW LEVEL SECURITY so the table OWNER is subject to the policies
-- from 0013. Run it by hand only as the final rollout step, after every runtime path connects as scribe_app (see the
-- ROLLOUT ORDER in 0013). Undo with: ALTER TABLE ... NO FORCE ROW LEVEL SECURITY.

-- Guard: refuse to run as a table owner that is not exempt from RLS, because FORCE would hide every row from that role.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_class c WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'
               AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user))
     AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND (rolsuper OR rolbypassrls)) THEN
    RAISE EXCEPTION 'refusing to FORCE RLS: % owns tables and has no BYPASSRLS; the owner connection would lose all rows. '
                    'Finish moving callers to scribe_app first.', current_user;
  END IF;
END $$;

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
    EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
