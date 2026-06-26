ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "updated_at" timestamp DEFAULT now() NOT NULL;
ALTER TABLE "usage_events" ADD COLUMN IF NOT EXISTS "idempotency_key" text;

UPDATE "files"
SET "updated_at" = COALESCE("created_at", now())
WHERE "updated_at" IS NULL;

ALTER TABLE "files" DROP CONSTRAINT IF EXISTS "files_status_check";
ALTER TABLE "files" ADD CONSTRAINT "files_status_check"
  CHECK ("status" IN ('uploading', 'queued', 'processing', 'retrying', 'ready', 'failed'));

CREATE INDEX IF NOT EXISTS "files_status_updated_at_index"
  ON "files" ("status", "updated_at");

CREATE UNIQUE INDEX IF NOT EXISTS "usage_events_idempotency_key_unique"
  ON "usage_events" ("idempotency_key");

WITH duplicate_jobs AS (
  SELECT "id",
    ROW_NUMBER() OVER (
      PARTITION BY "file_id"
      ORDER BY
        CASE "status" WHEN 'processing' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
        "updated_at" DESC,
        "id" DESC
    ) AS rank
  FROM "ingestion_jobs"
)
DELETE FROM "ingestion_jobs"
USING duplicate_jobs
WHERE "ingestion_jobs"."id" = duplicate_jobs."id"
  AND duplicate_jobs.rank > 1;

CREATE UNIQUE INDEX IF NOT EXISTS "ingestion_jobs_file_id_unique"
  ON "ingestion_jobs" ("file_id");

CREATE INDEX IF NOT EXISTS "ingestion_jobs_queue_index"
  ON "ingestion_jobs" ("status", "next_retry_at", "created_at");
