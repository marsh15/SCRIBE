-- Rollback for drizzle/0015_source_version_uploads.sql. Not journaled; run manually with psql.
BEGIN;
DROP FUNCTION IF EXISTS public.scribe_reserve_source_version(uuid, integer, text, text, bigint);
DROP FUNCTION IF EXISTS public.scribe_complete_source_version(uuid, uuid, text, text, bigint);
-- Restore the 0010 guard (only duplicate_of and upload_state may change on any row).
CREATE OR REPLACE FUNCTION scribe_guard_source_version_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.source_id IS DISTINCT FROM OLD.source_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
     OR NEW.version_number IS DISTINCT FROM OLD.version_number
     OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
     OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
     OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
     OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'source_versions row % is immutable (only duplicate_of and upload_state may change)', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;
-- Reserved-but-never-completed versions (upload_state='uploading', no sha256) are harmless; remove them if desired:
--   DELETE FROM source_versions WHERE upload_state = 'uploading' AND sha256 IS NULL;
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784505600000;
  END IF;
END $$;
COMMIT;
