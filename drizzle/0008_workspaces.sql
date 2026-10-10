-- Scribe 2.0 T03: personal workspaces. Idempotent: safe to run more than once.
-- RLS is intentionally NOT part of this migration (see ADR-0007, task T15).

CREATE TABLE IF NOT EXISTS "workspaces" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "kind" text NOT NULL,
  "owner_user_id" text,
  "created_at" timestamp DEFAULT now() NOT NULL,
  CONSTRAINT "workspaces_kind_check" CHECK ("kind" IN ('personal', 'eval'))
);

CREATE UNIQUE INDEX IF NOT EXISTS "workspaces_personal_owner_unique"
  ON "workspaces" ("owner_user_id") WHERE "kind" = 'personal';

CREATE TABLE IF NOT EXISTS "workspace_members" (
  "workspace_id" uuid NOT NULL,
  "user_id" text NOT NULL,
  "role" text DEFAULT 'owner' NOT NULL,
  CONSTRAINT "workspace_members_pkey" PRIMARY KEY ("workspace_id", "user_id")
);

DO $$ BEGIN
  ALTER TABLE "workspace_members"
    ADD CONSTRAINT "workspace_members_workspace_id_fk"
    FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS "workspace_members_user_id_index" ON "workspace_members" ("user_id");

ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "workspace_id" uuid;
ALTER TABLE "chats" ADD COLUMN IF NOT EXISTS "workspace_id" uuid;
ALTER TABLE "rag_traces" ADD COLUMN IF NOT EXISTS "workspace_id" uuid;

-- Backfill: one personal workspace per distinct user_id across the scoped tables.
INSERT INTO "workspaces" ("kind", "owner_user_id")
SELECT 'personal', u."user_id"
FROM (
  SELECT "user_id" FROM "files" WHERE "user_id" IS NOT NULL
  UNION
  SELECT "user_id" FROM "chats" WHERE "user_id" IS NOT NULL
  UNION
  SELECT "user_id" FROM "rag_traces" WHERE "user_id" IS NOT NULL
) AS u
ON CONFLICT DO NOTHING;

INSERT INTO "workspace_members" ("workspace_id", "user_id", "role")
SELECT w."id", w."owner_user_id", 'owner'
FROM "workspaces" AS w
WHERE w."kind" = 'personal' AND w."owner_user_id" IS NOT NULL
ON CONFLICT DO NOTHING;

UPDATE "files" AS t SET "workspace_id" = w."id"
FROM "workspaces" AS w
WHERE t."workspace_id" IS NULL AND w."kind" = 'personal' AND w."owner_user_id" = t."user_id";

UPDATE "chats" AS t SET "workspace_id" = w."id"
FROM "workspaces" AS w
WHERE t."workspace_id" IS NULL AND w."kind" = 'personal' AND w."owner_user_id" = t."user_id";

UPDATE "rag_traces" AS t SET "workspace_id" = w."id"
FROM "workspaces" AS w
WHERE t."workspace_id" IS NULL AND w."kind" = 'personal' AND w."owner_user_id" = t."user_id";

DO $$ BEGIN
  ALTER TABLE "files" ADD CONSTRAINT "files_workspace_id_workspaces_id_fk"
    FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id");
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chats" ADD CONSTRAINT "chats_workspace_id_workspaces_id_fk"
    FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id");
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "rag_traces" ADD CONSTRAINT "rag_traces_workspace_id_workspaces_id_fk"
    FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id");
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE "files" ALTER COLUMN "workspace_id" SET NOT NULL;
ALTER TABLE "chats" ALTER COLUMN "workspace_id" SET NOT NULL;
ALTER TABLE "rag_traces" ALTER COLUMN "workspace_id" SET NOT NULL;

CREATE INDEX IF NOT EXISTS "files_workspace_id_index" ON "files" ("workspace_id");
CREATE INDEX IF NOT EXISTS "chats_workspace_id_index" ON "chats" ("workspace_id");
CREATE INDEX IF NOT EXISTS "rag_traces_workspace_id_index" ON "rag_traces" ("workspace_id");
