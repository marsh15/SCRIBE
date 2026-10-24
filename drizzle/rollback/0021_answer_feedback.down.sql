-- Rollback for drizzle/0021_answer_feedback.sql. Not part of the migration journal; run manually with psql.
BEGIN;
DROP TABLE IF EXISTS public.answer_feedback CASCADE;
DROP INDEX IF EXISTS public.chat_messages_id_chat_id_uq;
DROP INDEX IF EXISTS public.rag_traces_id_workspace_uq;
DO $$
BEGIN
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784937600001;
  END IF;
END $$;
COMMIT;
