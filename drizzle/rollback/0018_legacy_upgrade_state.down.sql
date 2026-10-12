-- Rollback for drizzle/0018_legacy_upgrade_state.sql. Not journaled; run manually with psql.
-- Loses the "Re-upload to upgrade" marks (the backfill re-derives them on its next run).
BEGIN;
DROP INDEX IF EXISTS public.files_reupload_required_index;
ALTER TABLE public.files DROP COLUMN IF EXISTS reupload_reason;
ALTER TABLE public.files DROP COLUMN IF EXISTS reupload_required_at;
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784764800000;
  END IF;
END $$;
COMMIT;
