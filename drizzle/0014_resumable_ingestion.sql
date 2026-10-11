-- T18: resumable leased ingestion (DESIGN B3). Hand-written; never `drizzle-kit push` over RLS objects.
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "stage" text;
--> statement-breakpoint

-- One job per (file, version) instead of one per file, so a new version can queue while an older job is finished.
-- Jobs created before the version is registered (source_version_id IS NULL) still dedupe on the file.
DROP INDEX IF EXISTS "ingestion_jobs_file_id_unique";
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "ingestion_jobs_file_version_unique"
  ON "ingestion_jobs" ("file_id", COALESCE("source_version_id", '00000000-0000-0000-0000-000000000000'::uuid));
--> statement-breakpoint

-- Sweep now also fails the Source when the job ran out of attempts (nothing else would ever update it).
CREATE OR REPLACE FUNCTION public.app_sweep_expired_leases(p_max_attempts integer DEFAULT 5)
RETURNS TABLE(job_id integer)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
  WITH swept AS (
    UPDATE public.ingestion_jobs j
       SET status = CASE WHEN j.attempts >= p_max_attempts THEN 'failed' ELSE 'queued' END,
           error_code = CASE WHEN j.attempts >= p_max_attempts THEN 'lease_expired_max_attempts' ELSE j.error_code END,
           finished_at = CASE WHEN j.attempts >= p_max_attempts THEN now() ELSE j.finished_at END,
           lease_token = NULL, lease_expires_at = NULL, updated_at = now()
     WHERE j.status = 'processing' AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at < now()
    RETURNING j.id, j.file_id, j.status
  ), failed_files AS (
    UPDATE public.files f
       SET status = 'failed', processing_error = 'Indexing kept timing out. Try again.', updated_at = now()
      FROM swept s WHERE s.file_id = f.id AND s.status = 'failed' AND f.active_version_id IS NULL AND f.deleted_at IS NULL
    RETURNING f.id
  )
  SELECT id FROM swept
$$;
--> statement-breakpoint

-- Capacity / "is there more work" probes for the tick (counts only; no row data leaves the definer).
CREATE OR REPLACE FUNCTION public.app_count_live_leases()
RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT count(*)::integer FROM public.ingestion_jobs j
       WHERE j.status = 'processing' AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at >= now() $$;
--> statement-breakpoint
CREATE OR REPLACE FUNCTION public.app_count_claimable_jobs(p_max_attempts integer DEFAULT 5)
RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public
AS $$ SELECT count(*)::integer FROM public.ingestion_jobs j JOIN public.files f ON f.id = j.file_id
       WHERE f.deleted_at IS NULL AND j.attempts < p_max_attempts
         AND ((j.status IN ('queued', 'retrying') AND (j.next_retry_at IS NULL OR j.next_retry_at <= now()))
           OR (j.status = 'processing' AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at < now())) $$;
--> statement-breakpoint
REVOKE ALL ON FUNCTION public.app_count_live_leases(), public.app_count_claimable_jobs(integer) FROM PUBLIC;
--> statement-breakpoint
GRANT EXECUTE ON FUNCTION public.app_count_live_leases(), public.app_count_claimable_jobs(integer) TO scribe_app;
--> statement-breakpoint
ALTER FUNCTION public.app_count_live_leases() OWNER TO scribe_definer;
--> statement-breakpoint
ALTER FUNCTION public.app_count_claimable_jobs(integer) OWNER TO scribe_definer;
