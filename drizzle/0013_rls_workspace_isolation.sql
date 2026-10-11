-- T15: database-enforced workspace isolation (INV-1, ADR-0011). Idempotent. Rollback: drizzle/rollback/0013_rls_workspace_isolation.down.sql
--
-- Model
--   * `scribe_app`     NOLOGIN, NOBYPASSRLS, not an owner. The runtime role. An operator enables login out-of-band, e.g.
--                      ALTER ROLE scribe_app LOGIN PASSWORD :'from_secret_store';   (never put the password in a migration)
--                      and points DATABASE_URL / NEON_DATABASE_URL at it once callers set the workspace context.
--   * `scribe_definer` NOLOGIN owner of the SECURITY DEFINER functions. Instead of BYPASSRLS (needs superuser) it gets
--                      explicit permissive policies on the few queue tables the functions touch.
--   * Every workspace table: ENABLE ROW LEVEL SECURITY and one policy FOR ALL with USING and WITH CHECK comparing
--     workspace_id to NULLIF(current_setting('app.workspace_id', true), '')::uuid. No context => NULL => zero rows,
--     and writes fail. Superusers, BYPASSRLS roles and (because FORCE is NOT set here) the table owner are not affected,
--     so the legacy owner connection keeps working unchanged.
--   * Tables without a workspace_id column (chat_messages, ingestion_jobs, rag_trace_chunks, rag_evaluations, documents)
--     inherit through their parent row's workspace_id.
--   * Billing tables are keyed by user, not workspace, and are intentionally not RLS-protected (app-level user scoping).
--   * Future tables are NOT auto-granted to scribe_app (no ALTER DEFAULT PRIVILEGES): a new migration must grant + add RLS.
--
-- ROLLOUT ORDER (do not skip steps)
--   1. Apply this migration. Nothing changes for the owner connection; scribe_app is merely available.
--   2. Move callers to scoped()/set_config on a scribe_app connection (TS: lib/scoped.ts; Python already sets the context).
--      Enable LOGIN for scribe_app out of band and switch DATABASE_URL.
--   3. Only when no runtime path still connects as the owner, run drizzle/deferred/force_rls.sql (NOT journaled). FORCE makes
--      the owner subject to the policies; run on an owner without BYPASSRLS it would hide every row from the legacy app.
--   Rollback: drizzle/rollback/0013_rls_workspace_isolation.down.sql.
--
-- RE-RUNS: everything is idempotent for a superuser. The function section creates functions owned by scribe_definer, so
-- re-running it as a non-superuser needs `SET ROLE scribe_definer` first (CREATE OR REPLACE requires ownership); drizzle's
-- journal runs it once, so this only matters for manual re-application.
-- Never use `drizzle-kit push` against a database with these policies (see HANDOVER follow-ups).

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scribe_app') THEN
    CREATE ROLE scribe_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scribe_definer') THEN
    CREATE ROLE scribe_definer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END $$;
--> statement-breakpoint

DO $$
BEGIN
  -- only needed if the role pre-existed with BYPASSRLS; changing that attribute itself requires BYPASSRLS/superuser
  IF (SELECT rolbypassrls FROM pg_roles WHERE rolname = 'scribe_app') THEN
    ALTER ROLE scribe_app NOBYPASSRLS;
  END IF;
END $$;
--> statement-breakpoint

-- Policies -------------------------------------------------------------------------------------------------------
DO $$
DECLARE
  ctx constant text := 'NULLIF(current_setting(''app.workspace_id'', true), '''')::uuid';
  t text;
BEGIN
  -- direct workspace_id tables
  FOREACH t IN ARRAY ARRAY[
    'files','chats','rag_traces','source_versions','index_generations','active_indexes','document_blocks','chunks',
    'chunk_source_spans','chunk_embeddings_1536','chunk_embeddings_3072','retrieval_traces','retrieval_candidates',
    'evidence_bundles','evidence_items','claim_verifications','workspace_members']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON public.%I', t);
    EXECUTE format('CREATE POLICY workspace_isolation ON public.%I FOR ALL TO PUBLIC USING (workspace_id = %s) WITH CHECK (workspace_id = %s)', t, ctx, ctx);
  END LOOP;

  -- workspaces: the row itself is the workspace
  ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;
  ALTER TABLE public.workspaces NO FORCE ROW LEVEL SECURITY;
  DROP POLICY IF EXISTS workspace_isolation ON public.workspaces;
  EXECUTE format('CREATE POLICY workspace_isolation ON public.workspaces FOR ALL TO PUBLIC USING (id = %s) WITH CHECK (id = %s)', ctx, ctx);
END $$;
--> statement-breakpoint

DO $$
DECLARE
  ctx constant text := 'NULLIF(current_setting(''app.workspace_id'', true), '''')::uuid';
  r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('chat_messages',   'chat_id',  'chats'),
    ('ingestion_jobs',  'file_id',  'files'),
    ('documents',       'file_id',  'files'),
    ('rag_trace_chunks','trace_id', 'rag_traces'),
    ('rag_evaluations', 'trace_id', 'rag_traces')) AS v(child, fk, parent)
  LOOP
    CONTINUE WHEN to_regclass('public.' || r.child) IS NULL;
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.child);
    EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY', r.child);
    EXECUTE format('DROP POLICY IF EXISTS workspace_isolation ON public.%I', r.child);
    EXECUTE format(
      'CREATE POLICY workspace_isolation ON public.%I FOR ALL TO PUBLIC '
      'USING (EXISTS (SELECT 1 FROM public.%I p WHERE p.id = %I.%I AND p.workspace_id = %s)) '
      'WITH CHECK (EXISTS (SELECT 1 FROM public.%I p WHERE p.id = %I.%I AND p.workspace_id = %s))',
      r.child, r.parent, r.child, r.fk, ctx, r.parent, r.child, r.fk, ctx);
  END LOOP;
END $$;
--> statement-breakpoint

-- Grants for scribe_app -------------------------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO scribe_app, scribe_definer;
--> statement-breakpoint
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'files','chats','chat_messages','rag_traces','rag_trace_chunks','rag_evaluations','source_versions','index_generations',
    'active_indexes','document_blocks','chunks','chunk_source_spans','chunk_embeddings_1536','chunk_embeddings_3072',
    'retrieval_traces','retrieval_candidates','evidence_bundles','evidence_items','claim_verifications','ingestion_jobs',
    'documents','usage_events','billing_customers','billing_cycles','subscriptions','payment_events']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO scribe_app', t);
  END LOOP;
  -- read-only for the runtime: workspaces/members are created through app_ensure_personal_workspace; eval tables are
  -- written by the eval tooling (owner connection), never by the web runtime
  FOREACH t IN ARRAY ARRAY['workspaces','workspace_members','eval_runs','eval_results']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('REVOKE INSERT, UPDATE, DELETE ON public.%I FROM scribe_app', t);
    EXECUTE format('GRANT SELECT ON public.%I TO scribe_app', t);
  END LOOP;
  -- seeded, immutable configuration: read-only for the runtime role
  FOREACH t IN ARRAY ARRAY['embedding_profiles','index_profiles','chunk_profiles','retrieval_profiles']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('GRANT SELECT ON public.%I TO scribe_app, scribe_definer', t);
  END LOOP;
END $$;
--> statement-breakpoint
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO scribe_app, scribe_definer;
--> statement-breakpoint

-- scribe_definer: narrow table rights + permissive policies (instead of BYPASSRLS) ---------------------------------
GRANT SELECT, UPDATE ON public.ingestion_jobs TO scribe_definer;
--> statement-breakpoint
GRANT SELECT, UPDATE ON public.files TO scribe_definer;
--> statement-breakpoint
GRANT SELECT, INSERT ON public.workspaces TO scribe_definer;
--> statement-breakpoint
GRANT SELECT, INSERT ON public.workspace_members TO scribe_definer;
--> statement-breakpoint
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['ingestion_jobs','files','workspaces','workspace_members'] LOOP
    EXECUTE format('DROP POLICY IF EXISTS definer_access ON public.%I', t);
    EXECUTE format('CREATE POLICY definer_access ON public.%I FOR ALL TO scribe_definer USING (true) WITH CHECK (true)', t);
  END LOOP;
END $$;
--> statement-breakpoint

-- Let the migrating role hand function ownership to scribe_definer (needs SET on the role and CREATE on the schema).
DO $$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    EXECUTE format('GRANT scribe_definer TO %I WITH SET TRUE, INHERIT FALSE', current_user);
  END IF;
END $$;
--> statement-breakpoint
GRANT CREATE ON SCHEMA public TO scribe_definer;
--> statement-breakpoint
DROP FUNCTION IF EXISTS public.app_claim_ingestion_job(integer);
--> statement-breakpoint
DROP FUNCTION IF EXISTS public.app_sweep_expired_leases();
--> statement-breakpoint

-- SECURITY DEFINER functions (IDs only, fixed search_path) --------------------------------------------------------
-- SECURITY: p_user_id must be the authenticated session's user id (Clerk), never client input; a caller that can pass
-- any id can resolve any user's workspace id.
CREATE OR REPLACE FUNCTION public.app_workspace_ids_for_user(p_user_id text)
RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT m.workspace_id FROM public.workspace_members m WHERE m.user_id = p_user_id AND p_user_id <> '' ORDER BY m.workspace_id
$$;
--> statement-breakpoint

-- SECURITY: same rule as above for p_user_id.
CREATE OR REPLACE FUNCTION public.app_ensure_personal_workspace(p_user_id text)
RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE wid uuid;
BEGIN
  IF p_user_id IS NULL OR p_user_id = '' THEN
    RAISE EXCEPTION 'user id required';
  END IF;
  INSERT INTO public.workspaces(kind, owner_user_id) VALUES ('personal', p_user_id) ON CONFLICT DO NOTHING;
  SELECT w.id INTO wid FROM public.workspaces w WHERE w.kind = 'personal' AND w.owner_user_id = p_user_id;
  INSERT INTO public.workspace_members(workspace_id, user_id, role) VALUES (wid, p_user_id, 'owner') ON CONFLICT DO NOTHING;
  RETURN wid;
END $$;
--> statement-breakpoint

-- Claim one runnable job (queued and due, or processing with an expired lease) that has attempts left and whose source
-- is not soft-deleted. Lease length is clamped to 1..3600 s. Returns ids + the new lease token only.
CREATE OR REPLACE FUNCTION public.app_claim_ingestion_job(p_lease_seconds integer DEFAULT 300, p_max_attempts integer DEFAULT 5)
RETURNS TABLE(job_id integer, file_id integer, workspace_id uuid, lease_token uuid)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
DECLARE jid integer;
BEGIN
  SELECT j.id INTO jid FROM public.ingestion_jobs j JOIN public.files f ON f.id = j.file_id
   WHERE f.deleted_at IS NULL AND j.attempts < p_max_attempts
     AND ((j.status IN ('queued', 'retrying') AND (j.next_retry_at IS NULL OR j.next_retry_at <= now()))
       OR (j.status = 'processing' AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at < now()))
   ORDER BY j.created_at, j.id
   FOR UPDATE OF j SKIP LOCKED
   LIMIT 1;
  IF jid IS NULL THEN RETURN; END IF;
  RETURN QUERY
  UPDATE public.ingestion_jobs u
     SET status = 'processing', attempts = u.attempts + 1, lease_token = gen_random_uuid(),
         lease_expires_at = now() + make_interval(secs => LEAST(GREATEST(p_lease_seconds, 1), 3600)),
         started_at = COALESCE(u.started_at, now()), updated_at = now()
   WHERE u.id = jid
  RETURNING u.id, u.file_id, (SELECT f.workspace_id FROM public.files f WHERE f.id = u.file_id), u.lease_token;
END $$;
--> statement-breakpoint

-- Sweep: expired leases go back to the queue, or to 'failed' once attempts are exhausted. Returns the affected job ids.
CREATE OR REPLACE FUNCTION public.app_sweep_expired_leases(p_max_attempts integer DEFAULT 5)
RETURNS TABLE(job_id integer)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  UPDATE public.ingestion_jobs j
     SET status = CASE WHEN j.attempts >= p_max_attempts THEN 'failed' ELSE 'queued' END,
         error_code = CASE WHEN j.attempts >= p_max_attempts THEN 'lease_expired_max_attempts' ELSE j.error_code END,
         finished_at = CASE WHEN j.attempts >= p_max_attempts THEN now() ELSE j.finished_at END,
         lease_token = NULL, lease_expires_at = NULL, updated_at = now()
   WHERE j.status = 'processing' AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at < now()
  RETURNING j.id
$$;
--> statement-breakpoint

-- Sweep: soft-deleted sources past the grace period, for the background purge. Read-only; returns ids.
CREATE OR REPLACE FUNCTION public.app_list_purgeable_sources(p_older_than_seconds integer DEFAULT 86400, p_limit integer DEFAULT 100)
RETURNS TABLE(source_id integer, workspace_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT f.id, f.workspace_id FROM public.files f
   WHERE f.deleted_at IS NOT NULL AND f.deleted_at < now() - make_interval(secs => p_older_than_seconds)
   ORDER BY f.deleted_at LIMIT GREATEST(p_limit, 0)
$$;
--> statement-breakpoint

-- privileges first: after OWNER TO the migrating role no longer owns the functions and cannot change their ACL
REVOKE ALL ON FUNCTION public.app_workspace_ids_for_user(text), public.app_ensure_personal_workspace(text),
  public.app_claim_ingestion_job(integer, integer), public.app_sweep_expired_leases(integer),
  public.app_list_purgeable_sources(integer, integer) FROM PUBLIC;
--> statement-breakpoint
GRANT EXECUTE ON FUNCTION public.app_workspace_ids_for_user(text), public.app_ensure_personal_workspace(text),
  public.app_claim_ingestion_job(integer, integer), public.app_sweep_expired_leases(integer),
  public.app_list_purgeable_sources(integer, integer) TO scribe_app;
--> statement-breakpoint
ALTER FUNCTION public.app_workspace_ids_for_user(text) OWNER TO scribe_definer;
--> statement-breakpoint
ALTER FUNCTION public.app_ensure_personal_workspace(text) OWNER TO scribe_definer;
--> statement-breakpoint
ALTER FUNCTION public.app_claim_ingestion_job(integer, integer) OWNER TO scribe_definer;
--> statement-breakpoint
ALTER FUNCTION public.app_sweep_expired_leases(integer) OWNER TO scribe_definer;
--> statement-breakpoint
ALTER FUNCTION public.app_list_purgeable_sources(integer, integer) OWNER TO scribe_definer;
--> statement-breakpoint
REVOKE CREATE ON SCHEMA public FROM scribe_definer;
