-- Rollback for drizzle/0017_ocr_pages.sql. Not journaled; run manually with psql.
-- Drops the OCR progress table (re-OCR of any in-flight scanned PDF will happen again after re-applying).
BEGIN;
DROP TABLE IF EXISTS public.source_version_ocr_pages;
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784678400000;
  END IF;
END $$;
COMMIT;
