-- T15 follow-up: cross-workspace referential integrity. Idempotent. Rollback: drizzle/rollback/0019_workspace_composite_fks.down.sql
--
-- RI checks run as the table owner and bypass RLS, so a child row could point at a parent in ANOTHER workspace (the RLS
-- policy only looks at the child's own workspace_id). Each relationship below gets a composite FK
-- (child_col, workspace_id) -> parent(id, workspace_id), which needs UNIQUE (id, workspace_id) on the parent.
-- The original single-column FKs stay (they keep drizzle's schema and the ON DELETE actions unchanged).
-- Only relationships where BOTH tables carry workspace_id (always NOT NULL) are covered. chat_messages, ingestion_jobs,
-- documents, rag_trace_chunks, rag_evaluations have no workspace_id and inherit through RLS EXISTS policies instead.
--
-- Nullable child columns whose single-column FK is ON DELETE SET NULL use the PG15+ column-list form
-- `ON DELETE SET NULL (col)` so workspace_id is never nulled. On PG < 15 those composite FKs are skipped with a NOTICE.
--
-- Pre-check: if existing rows already violate any relationship the migration aborts BEFORE changing anything, listing
-- the offending relationships and counts. Find rows with the query printed in the error, fix or delete them, re-run.

DO $$
DECLARE
  r record;
  bad bigint;
  msg text := '';
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('active_indexes','source_id','files'),
    ('chunk_embeddings_1536','chunk_id','chunks'),
    ('chunk_embeddings_3072','chunk_id','chunks'),
    ('chunk_source_spans','chunk_id','chunks'),
    ('chunk_source_spans','block_id','document_blocks'),
    ('chunks','generation_id','index_generations'),
    ('chunks','source_version_id','source_versions'),
    ('chunks','parent_id','chunks'),
    ('document_blocks','source_version_id','source_versions'),
    ('evidence_bundles','trace_id','retrieval_traces'),
    ('evidence_items','bundle_id','evidence_bundles'),
    ('evidence_items','chunk_id','chunks'),
    ('evidence_items','source_version_id','source_versions'),
    ('files','desired_version_id','source_versions'),
    ('files','active_version_id','source_versions'),
    ('index_generations','source_id','files'),
    ('rag_traces','chat_id','chats'),
    ('retrieval_candidates','trace_id','retrieval_traces'),
    ('retrieval_candidates','chunk_id','chunks'),
    ('retrieval_traces','chat_id','chats'),
    ('source_version_ocr_pages','source_version_id','source_versions'),
    ('source_versions','source_id','files'),
    ('source_versions','duplicate_of','source_versions')
  ) AS v(child, col, parent)
  LOOP
    CONTINUE WHEN to_regclass('public.' || r.child) IS NULL OR to_regclass('public.' || r.parent) IS NULL;
    EXECUTE format(
      'SELECT count(*) FROM public.%I c JOIN public.%I p ON p.id = c.%I WHERE c.workspace_id IS DISTINCT FROM p.workspace_id',
      r.child, r.parent, r.col) INTO bad;
    IF bad > 0 THEN
      msg := msg || format(E'\n  %s.%s -> %s: %s row(s). Find them: SELECT c.id FROM %I c JOIN %I p ON p.id = c.%I WHERE c.workspace_id IS DISTINCT FROM p.workspace_id;',
                           r.child, r.col, r.parent, bad, r.child, r.parent, r.col);
    END IF;
  END LOOP;
  IF msg <> '' THEN
    RAISE EXCEPTION E'0019 aborted: rows reference a parent in a different workspace. Fix or delete them, then re-run.%', msg;
  END IF;
END $$;
--> statement-breakpoint

-- Parent uniqueness (id, workspace_id) --------------------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['files','chats','source_versions','index_generations','chunks','document_blocks','retrieval_traces','evidence_bundles']
  LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = ('public.' || t)::regclass AND conname = t || '_id_workspace_uq') THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I UNIQUE (id, workspace_id)', t, t || '_id_workspace_uq');
    END IF;
  END LOOP;
END $$;
--> statement-breakpoint

-- Composite FKs -------------------------------------------------------------------------------------------------------
-- action: 'cascade' | 'setnull' | 'none'
DO $$
DECLARE
  r record;
  fk text;
  act text;
BEGIN
  FOR r IN SELECT * FROM (VALUES
    ('active_indexes','source_id','files','cascade'),
    ('chunk_embeddings_1536','chunk_id','chunks','cascade'),
    ('chunk_embeddings_3072','chunk_id','chunks','cascade'),
    ('chunk_source_spans','chunk_id','chunks','cascade'),
    ('chunk_source_spans','block_id','document_blocks','cascade'),
    ('chunks','generation_id','index_generations','cascade'),
    ('chunks','source_version_id','source_versions','cascade'),
    ('chunks','parent_id','chunks','setnull'),
    ('document_blocks','source_version_id','source_versions','cascade'),
    ('evidence_bundles','trace_id','retrieval_traces','cascade'),
    ('evidence_items','bundle_id','evidence_bundles','cascade'),
    ('evidence_items','chunk_id','chunks','setnull'),
    ('evidence_items','source_version_id','source_versions','setnull'),
    ('files','desired_version_id','source_versions','setnull'),
    ('files','active_version_id','source_versions','setnull'),
    ('index_generations','source_id','files','cascade'),
    ('rag_traces','chat_id','chats','cascade'),
    ('retrieval_candidates','trace_id','retrieval_traces','cascade'),
    ('retrieval_candidates','chunk_id','chunks','setnull'),
    ('retrieval_traces','chat_id','chats','setnull'),
    ('source_version_ocr_pages','source_version_id','source_versions','cascade'),
    ('source_versions','source_id','files','cascade'),
    ('source_versions','duplicate_of','source_versions','setnull')
  ) AS v(child, col, parent, action)
  LOOP
    CONTINUE WHEN to_regclass('public.' || r.child) IS NULL OR to_regclass('public.' || r.parent) IS NULL;
    fk := left(r.child || '_' || r.col || '_ws_fk', 63);
    CONTINUE WHEN EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = ('public.' || r.child)::regclass AND conname = fk);
    IF r.action = 'cascade' THEN
      act := 'ON DELETE CASCADE';
    ELSIF current_setting('server_version_num')::int >= 150000 THEN
      act := format('ON DELETE SET NULL (%I)', r.col);
    ELSE
      RAISE NOTICE '0019: skipping %.% (needs PG15+ for ON DELETE SET NULL (col))', r.child, r.col;
      CONTINUE;
    END IF;
    EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I FOREIGN KEY (%I, workspace_id) REFERENCES public.%I (id, workspace_id) %s',
                   r.child, fk, r.col, r.parent, act);
  END LOOP;
END $$;
