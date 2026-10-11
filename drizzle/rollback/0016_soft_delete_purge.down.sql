-- Rollback for drizzle/0016_soft_delete_purge.sql. Not journaled; run manually with psql.
BEGIN;
DROP FUNCTION IF EXISTS public.scribe_soft_delete_source(uuid, text, integer);
-- restore the 0013 body (no purge backoff filter)
CREATE OR REPLACE FUNCTION public.app_list_purgeable_sources(p_older_than_seconds integer DEFAULT 86400, p_limit integer DEFAULT 100)
RETURNS TABLE(source_id integer, workspace_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  SELECT f.id, f.workspace_id FROM public.files f
   WHERE f.deleted_at IS NOT NULL AND f.deleted_at < now() - make_interval(secs => p_older_than_seconds)
   ORDER BY f.deleted_at LIMIT GREATEST(p_limit, 0)
$$;
ALTER TABLE public.files DROP COLUMN IF EXISTS purge_attempts;
ALTER TABLE public.files DROP COLUMN IF EXISTS purge_next_retry_at;
ALTER TABLE public.files DROP COLUMN IF EXISTS purge_error_code;
ALTER TABLE public.files DROP COLUMN IF EXISTS purge_last_error;
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784592000000;
  END IF;
END $$;
COMMIT;
