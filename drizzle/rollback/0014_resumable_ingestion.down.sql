-- Rollback for drizzle/0014_resumable_ingestion.sql. Not journaled; run manually with psql.
BEGIN;
DROP FUNCTION IF EXISTS public.app_count_live_leases();
DROP FUNCTION IF EXISTS public.app_count_claimable_jobs(integer);
DROP INDEX IF EXISTS public.ingestion_jobs_file_version_unique;
-- Fails if a file now has several jobs: delete the extra (older) jobs first.
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_jobs_file_id_unique ON public.ingestion_jobs (file_id);
ALTER TABLE public.ingestion_jobs DROP COLUMN IF EXISTS stage;
-- app_sweep_expired_leases keeps the 0014 body (a superset of the 0013 behaviour); re-run 0013's definition to restore it.
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784419200000;
  END IF;
END $$;
COMMIT;
