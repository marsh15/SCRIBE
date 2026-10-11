import { sql } from "@/lib/db-config";

/** Make a queued/retrying Source's job due now ("Process now"). Returns true when a job was found. */
export async function markSourceDue(workspaceId: string, sourceId: number): Promise<boolean> {
  const rows = await sql`
    UPDATE "ingestion_jobs" AS job
    SET "next_retry_at" = NULL, "updated_at" = now()
    FROM "files" AS source
    WHERE job."file_id" = source."id"
      AND source."id" = ${sourceId}
      AND source."workspace_id" = ${workspaceId}
      AND source."deleted_at" IS NULL
      AND job."status" IN ('queued', 'retrying')
    RETURNING job."id"
  `;
  return rows.length > 0;
}

/**
 * Retry button: put a FAILED Source back in the queue with a fresh attempt budget. Restarts from PARSE
 * (stage NULL); chunks and embeddings that already exist are reused by the indexer, not duplicated.
 */
export async function requeueFailedSource(workspaceId: string, sourceId: number): Promise<boolean> {
  const requeued = await sql`
    WITH source AS (
      UPDATE "files"
      SET "status" = 'queued', "processing_error" = NULL, "updated_at" = now()
      WHERE "id" = ${sourceId} AND "workspace_id" = ${workspaceId} AND "status" = 'failed' AND "deleted_at" IS NULL
      RETURNING "id"
    ), job AS (
      UPDATE "ingestion_jobs" AS j
      SET "status" = 'queued', "attempts" = 0, "stage" = NULL, "error_code" = NULL, "last_error" = NULL,
          "next_retry_at" = NULL, "finished_at" = NULL, "lease_token" = NULL, "lease_expires_at" = NULL,
          "updated_at" = now()
      FROM source
      WHERE j."file_id" = source."id" AND j."status" IN ('failed', 'cancelled')
      RETURNING j."id"
    ), created AS (
      INSERT INTO "ingestion_jobs" ("file_id", "status", "attempts", "created_at", "updated_at")
      SELECT "id", 'queued', 0, now(), now() FROM source
      WHERE NOT EXISTS (SELECT 1 FROM job)
      ON CONFLICT DO NOTHING
      RETURNING "id"
    )
    SELECT (SELECT count(*) FROM source) AS n
  ` as Array<{ n: string | number }>;
  return Number(requeued[0]?.n ?? 0) > 0;
}
