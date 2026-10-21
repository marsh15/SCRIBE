-- Rollback for drizzle/0020_legacy_workspace_default.sql. Not journaled; run manually with psql.
-- Drops the three BEFORE INSERT triggers and the trigger function. After this the Scribe 1.0 build can no longer insert
-- chats, files or rag_traces (workspace_id is NOT NULL again without a default), so only run it once 1.0 is no longer a
-- rollback target. Rows the shim already filled in keep their workspace_id. Needs the table owner and, for the function,
-- membership in scribe_definer (granted by 0013/0020 to a non-superuser migrator).
BEGIN;
DROP TRIGGER IF EXISTS files_legacy_default_workspace ON public.files;
DROP TRIGGER IF EXISTS chats_legacy_default_workspace ON public.chats;
DROP TRIGGER IF EXISTS rag_traces_legacy_default_workspace ON public.rag_traces;
DROP FUNCTION IF EXISTS public.app_legacy_default_workspace();
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784937600000;
  END IF;
END $$;
COMMIT;
