-- T22: visible "Re-upload to upgrade" state for legacy Sources that cannot be rebuilt on structural-v1 because their
-- original blob is missing or unreadable. The legacy generation stays ACTIVE and searchable (legacy-dense strategy);
-- the retire guard in drizzle/deferred/retire_legacy.sql accepts a Source that carries this mark.
-- Idempotent: safe to replay on a database whose schema was exported from lib/db-schema.ts.
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "reupload_required_at" timestamp with time zone;
--> statement-breakpoint
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "reupload_reason" text;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "files_reupload_required_index" ON "files" ("workspace_id") WHERE "reupload_required_at" IS NOT NULL;
