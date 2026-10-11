-- T20: soft delete that takes effect in one transaction (INV-14), plus the bookkeeping the background purge needs.
-- Hand-written and idempotent. Rollback: drizzle/rollback/0016_soft_delete_purge.down.sql. Never `drizzle-kit push` over RLS objects.
--
-- 1. files.purge_* : retry state of the purge (attempts, next due time, last error code/message). A Source whose purge
--    keeps failing therefore stays visible to an operator (select ... where deleted_at is not null and purge_error_code is not null).
-- 2. app_list_purgeable_sources: same signature and grace-period semantics as 0013, but skips Sources whose purge backoff
--    has not elapsed.
-- 3. scribe_soft_delete_source: ONE function = ONE transaction that sets deleted_at and cancels the Source's queued /
--    processing / retrying jobs (lease cleared, so a worker that still holds the lease loses it: INV-5, its fenced writes
--    match zero rows). Retrieval and evidence reads filter on files.deleted_at, so the Source is gone from search the moment
--    this commits. SECURITY INVOKER like scribe_reserve_source_version (0015): it sets the workspace context itself.

ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "purge_attempts" integer DEFAULT 0 NOT NULL;
--> statement-breakpoint
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "purge_next_retry_at" timestamp with time zone;
--> statement-breakpoint
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "purge_error_code" text;
--> statement-breakpoint
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "purge_last_error" text;
--> statement-breakpoint

CREATE OR REPLACE FUNCTION public.app_list_purgeable_sources(p_older_than_seconds integer DEFAULT 86400, p_limit integer DEFAULT 100)
RETURNS TABLE(source_id integer, workspace_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT f.id, f.workspace_id FROM public.files f
   WHERE f.deleted_at IS NOT NULL AND f.deleted_at < now() - make_interval(secs => p_older_than_seconds)
     AND (f.purge_next_retry_at IS NULL OR f.purge_next_retry_at <= now())
   ORDER BY f.deleted_at LIMIT GREATEST(p_limit, 0)
$$;
--> statement-breakpoint
REVOKE ALL ON FUNCTION public.app_list_purgeable_sources(integer, integer) FROM PUBLIC;
--> statement-breakpoint
GRANT EXECUTE ON FUNCTION public.app_list_purgeable_sources(integer, integer) TO scribe_app;
--> statement-breakpoint
ALTER FUNCTION public.app_list_purgeable_sources(integer, integer) OWNER TO scribe_definer;
--> statement-breakpoint

-- Returns true when this call deleted the Source, false when it does not exist for this owner or was already deleted.
CREATE OR REPLACE FUNCTION public.scribe_soft_delete_source(p_workspace uuid, p_user text, p_source integer)
RETURNS boolean
LANGUAGE plpgsql SET search_path = pg_catalog, public
AS $$
BEGIN
  PERFORM set_config('app.workspace_id', p_workspace::text, true);
  UPDATE public.files
     SET deleted_at = now(), updated_at = now(),
         purge_attempts = 0, purge_next_retry_at = NULL, purge_error_code = NULL, purge_last_error = NULL
   WHERE id = p_source AND workspace_id = p_workspace AND user_id = p_user AND deleted_at IS NULL;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  UPDATE public.ingestion_jobs
     SET status = 'cancelled', stage = 'CANCELLED', error_code = 'SOURCE_DELETED', last_error = 'source was deleted',
         lease_token = NULL, lease_expires_at = NULL, next_retry_at = NULL, finished_at = now(), updated_at = now()
   WHERE file_id = p_source AND status IN ('queued', 'processing', 'retrying');
  RETURN true;
END $$;
--> statement-breakpoint
REVOKE ALL ON FUNCTION public.scribe_soft_delete_source(uuid, text, integer) FROM PUBLIC;
--> statement-breakpoint
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scribe_app') THEN
    GRANT EXECUTE ON FUNCTION public.scribe_soft_delete_source(uuid, text, integer) TO scribe_app;
  END IF;
END $$;
