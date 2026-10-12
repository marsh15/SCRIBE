-- T21: OCR for scanned PDF pages. Hand-written and idempotent. Rollback: drizzle/rollback/0017_ocr_pages.down.sql.
-- Never `drizzle-kit push` over RLS objects.
--
-- source_version_ocr_pages is the durable PROGRESS of OCR for one source version: one row per page that has been
-- transcribed (text may be '' for a blank page, which still counts as done). A killed or time-boxed step resumes by
-- reading these rows and OCR-ing only the pages that have none. Rows are written by the leased worker in the same
-- transaction as the lease fence and the idempotent usage event, and they cascade away with the version (so the
-- soft-delete purge needs no extra step). The text is untrusted document content (INV-13): it is never logged.
-- RLS and grants follow the 0013 pattern (new tables are NOT auto-granted).

CREATE TABLE IF NOT EXISTS "source_version_ocr_pages" (
  "source_version_id" uuid NOT NULL REFERENCES "source_versions"("id") ON DELETE CASCADE,
  "workspace_id" uuid NOT NULL REFERENCES "workspaces"("id"),
  "page" integer NOT NULL,
  "text" text NOT NULL,
  "model" text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT "source_version_ocr_pages_pk" PRIMARY KEY ("source_version_id", "page"),
  CONSTRAINT "source_version_ocr_pages_page_check" CHECK ("page" >= 1)
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "source_version_ocr_pages_workspace_id_index" ON "source_version_ocr_pages" ("workspace_id");
--> statement-breakpoint
ALTER TABLE public.source_version_ocr_pages ENABLE ROW LEVEL SECURITY;
--> statement-breakpoint
ALTER TABLE public.source_version_ocr_pages NO FORCE ROW LEVEL SECURITY;
--> statement-breakpoint
DROP POLICY IF EXISTS workspace_isolation ON public.source_version_ocr_pages;
--> statement-breakpoint
CREATE POLICY workspace_isolation ON public.source_version_ocr_pages FOR ALL TO PUBLIC
  USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
  WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid);
--> statement-breakpoint
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scribe_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.source_version_ocr_pages TO scribe_app;
  END IF;
END $$;
