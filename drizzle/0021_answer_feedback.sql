-- A11: collect answer ratings and link them to the source chat and retrieval trace when available.
-- Expand-first and idempotent. Rollback: drizzle/rollback/0021_answer_feedback.down.sql

CREATE TABLE IF NOT EXISTS public.answer_feedback (
  id serial PRIMARY KEY,
  workspace_id uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
  chat_id text NOT NULL REFERENCES public.chats(id) ON DELETE CASCADE,
  message_id text NOT NULL REFERENCES public.chat_messages(id) ON DELETE CASCADE,
  rag_trace_id text REFERENCES public.rag_traces(id) ON DELETE SET NULL,
  retrieval_trace_id uuid REFERENCES public.retrieval_traces(id) ON DELETE SET NULL,
  user_id text NOT NULL,
  rating text NOT NULL,
  reasons text[] NOT NULL DEFAULT '{}',
  comment text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT answer_feedback_rating_check CHECK (rating IN ('up', 'down')),
  CONSTRAINT answer_feedback_reasons_check CHECK (
    reasons <@ ARRAY['wrong_citation', 'missing_info', 'outdated_version', 'should_abstain']::text[]
  ),
  CONSTRAINT answer_feedback_comment_length_check CHECK (comment IS NULL OR char_length(comment) <= 2000)
);
--> statement-breakpoint

-- A feedback message must be from the same chat. The primary key on id already makes this index unique in practice;
-- this composite target lets Postgres enforce that the two submitted references agree.
CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_id_chat_id_uq ON public.chat_messages (id, chat_id);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS rag_traces_id_workspace_uq ON public.rag_traces (id, workspace_id);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS answer_feedback_workspace_created_at_index
  ON public.answer_feedback (workspace_id, created_at);
--> statement-breakpoint

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conrelid = 'public.answer_feedback'::regclass
      AND conname = 'answer_feedback_message_chat_fk'
  ) THEN
    ALTER TABLE public.answer_feedback ADD CONSTRAINT answer_feedback_message_chat_fk
      FOREIGN KEY (message_id, chat_id) REFERENCES public.chat_messages (id, chat_id) ON DELETE CASCADE;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conrelid = 'public.answer_feedback'::regclass
      AND conname = 'answer_feedback_chat_workspace_fk'
  ) THEN
    ALTER TABLE public.answer_feedback ADD CONSTRAINT answer_feedback_chat_workspace_fk
      FOREIGN KEY (chat_id, workspace_id) REFERENCES public.chats (id, workspace_id) ON DELETE CASCADE;
  END IF;

  IF current_setting('server_version_num')::int >= 150000 THEN
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conrelid = 'public.answer_feedback'::regclass
        AND conname = 'answer_feedback_rag_trace_workspace_fk'
    ) THEN
      ALTER TABLE public.answer_feedback ADD CONSTRAINT answer_feedback_rag_trace_workspace_fk
        FOREIGN KEY (rag_trace_id, workspace_id) REFERENCES public.rag_traces (id, workspace_id)
        ON DELETE SET NULL (rag_trace_id);
    END IF;

    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint WHERE conrelid = 'public.answer_feedback'::regclass
        AND conname = 'answer_feedback_retrieval_trace_workspace_fk'
    ) THEN
      ALTER TABLE public.answer_feedback ADD CONSTRAINT answer_feedback_retrieval_trace_workspace_fk
        FOREIGN KEY (retrieval_trace_id, workspace_id) REFERENCES public.retrieval_traces (id, workspace_id)
        ON DELETE SET NULL (retrieval_trace_id);
    END IF;
  ELSE
    RAISE NOTICE '0021: skipping composite trace workspace FKs (needs PG15+ for column-list ON DELETE SET NULL)';
  END IF;
END $$;
--> statement-breakpoint

ALTER TABLE public.answer_feedback ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE public.answer_feedback NO FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS workspace_isolation ON public.answer_feedback;
--> statement-breakpoint
CREATE POLICY workspace_isolation ON public.answer_feedback FOR ALL TO PUBLIC
  USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
--> statement-breakpoint

GRANT SELECT, INSERT, UPDATE, DELETE ON public.answer_feedback TO scribe_app;
--> statement-breakpoint
GRANT USAGE, SELECT ON SEQUENCE public.answer_feedback_id_seq TO scribe_app;
