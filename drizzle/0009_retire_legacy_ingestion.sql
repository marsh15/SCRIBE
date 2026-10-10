-- T04: the legacy in-process ingestion path (lib/ingestion/worker.ts and the
-- batch/complete routes) is removed. Sources still in flight without a private
-- Blob original cannot be resumed by Source intake, so mark them failed with a
-- re-upload message. Safe to run repeatedly.
UPDATE "files"
SET "status" = 'failed',
    "processing_error" = 'This upload used a retired ingestion path. Please upload the file again.',
    "updated_at" = now()
WHERE "status" IN ('uploading', 'queued', 'processing', 'retrying')
  AND "storage_url" IS NULL
  AND "storage_key" IS NULL;

DELETE FROM "ingestion_jobs"
WHERE "file_id" IN (
  SELECT "id" FROM "files"
  WHERE "status" = 'failed' AND "storage_url" IS NULL AND "storage_key" IS NULL
);
