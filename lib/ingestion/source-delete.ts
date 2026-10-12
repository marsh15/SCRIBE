import { sql } from "@/lib/db-config";
import { env } from "@/lib/env";
import { deleteBlob } from "@/lib/storage/blob";

/**
 * Deleting a Source has two modes (P0-3):
 *
 *  - Python engine configured (INGEST_ENGINE=python AND INTERNAL_SERVICE_SECRET): SOFT delete (INV-14); the Python
 *    purge removes blobs and rows after the grace period.
 *  - Anything else (INGEST_ENGINE=legacy, the default, or no internal secret): nothing would ever run that purge, so the
 *    delete is a HARD delete, like Scribe 1.0: blobs of the original and of every version, then the file row, which
 *    cascades to documents, versions, generations, chunks, embeddings, spans, blocks and jobs.
 *
 * The hard delete mirrors intelligence/src/scribe_ingest/purge.py (blobs first, then ONE statement for the rows,
 * evidence quotes and legacy trace previews scrubbed because they are copies of document text).
 */
export function pythonEngineConfigured(): boolean {
  return env.INGEST_ENGINE === "python" && Boolean(env.INTERNAL_SERVICE_SECRET);
}

/** Cleanup batch per sweep: bounded so one sweep never runs past the function limit. */
export const LEGACY_PURGE_BATCH = 20;

export type HardDeleteOutcome = "deleted" | "missing" | "blob_failed";

interface SourceRefs {
  storage_url: string | null;
  storage_key: string | null;
  version_keys: Array<string | null> | null;
}

function blobIdentity(ref: string): string {
  if (/^https?:\/\//.test(ref)) {
    try {
      return new URL(ref).pathname.replace(/^\/+/, "");
    } catch {
      return ref;
    }
  }
  return ref.replace(/^\/+/, "");
}

/** Unique blobs, URL form preferred when a URL and a pathname name the same blob. */
export function dedupeBlobRefs(refs: Array<string | null | undefined>): string[] {
  const chosen = new Map<string, string>();
  for (const ref of refs) {
    if (!ref) continue;
    const key = blobIdentity(ref);
    const current = chosen.get(key);
    if (current === undefined || (ref.startsWith("http") && !current.startsWith("http"))) chosen.set(key, ref);
  }
  return [...chosen.values()];
}

/**
 * Hard-delete one Source owned by (workspaceId, userId). `onlySoftDeleted` is for the cleanup of rows left behind by
 * the interim soft-delete behaviour. Blobs go first and a blob failure leaves the rows (soft-deleted by the caller)
 * so nothing becomes unreachable; blob deletion is idempotent so a retry is safe.
 */
export async function hardDeleteSource(input: {
  workspaceId: string;
  userId: string | null;
  sourceId: number;
  onlySoftDeleted?: boolean;
}): Promise<HardDeleteOutcome> {
  const { workspaceId, userId, sourceId } = input;
  const onlySoftDeleted = input.onlySoftDeleted === true;
  const found = (await sql`
    SELECT f."storage_url", f."storage_key",
           ARRAY(SELECT v."storage_key" FROM "source_versions" v
                  WHERE v."source_id" = f."id" AND v."workspace_id" = f."workspace_id") AS version_keys
      FROM "files" f
     WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
       AND (${userId}::text IS NULL OR f."user_id" = ${userId})
       AND (NOT ${onlySoftDeleted}::boolean OR f."deleted_at" IS NOT NULL)
  `) as unknown as SourceRefs[];
  const row = found[0];
  if (!row) return "missing";

  const refs = dedupeBlobRefs([row.storage_url, ...(row.version_keys ?? []), row.storage_key]);
  for (const ref of refs) {
    try {
      await deleteBlob(ref);
    } catch (error) {
      console.error("[SourceIntake] blob delete failed", {
        sourceId,
        error: error instanceof Error ? error.name : "unknown",
      });
      // Back off like the Python purge (purge_* columns, migration 0016) so a stuck blob cannot starve the batch.
      await sql`
        UPDATE "files"
           SET "purge_attempts" = "purge_attempts" + 1, "purge_error_code" = 'BLOB_DELETE_FAILED',
               "purge_last_error" = 'blob delete failed', "updated_at" = now(),
               "purge_next_retry_at" = now() + make_interval(mins => LEAST(1440, power(2, LEAST("purge_attempts" + 1, 11)))::int)
         WHERE "id" = ${sourceId}::int AND "workspace_id" = ${workspaceId}::uuid
      `;
      return "blob_failed";
    }
  }

  // ONE statement (data-modifying CTEs commit or roll back together); the files delete cascades to the rest.
  const removed = (await sql`
    WITH src AS (
      SELECT "id" FROM "files"
       WHERE "id" = ${sourceId}::int AND "workspace_id" = ${workspaceId}::uuid
         AND (${userId}::text IS NULL OR "user_id" = ${userId})
         AND (NOT ${onlySoftDeleted}::boolean OR "deleted_at" IS NOT NULL)
    ),
    ev AS (
      UPDATE "evidence_items" e SET "quote" = ''
        FROM "source_versions" v
       WHERE v."id" = e."source_version_id" AND v."source_id" IN (SELECT "id" FROM src)
         AND e."workspace_id" = ${workspaceId}::uuid AND e."quote" <> ''
      RETURNING e."id"
    ),
    tr AS (
      UPDATE "rag_trace_chunks" SET "content_preview" = '', "metadata" = NULL
       WHERE "file_id" IN (SELECT "id" FROM src)
      RETURNING "id"
    )
    DELETE FROM "files" WHERE "id" IN (SELECT "id" FROM src) RETURNING "id"
  `) as unknown as Array<{ id: number }>;
  return removed.length > 0 ? "deleted" : "missing";
}

export interface LegacyPurgeSummary {
  listed: number;
  purged: number;
  blobFailed: number;
}

/**
 * Idempotent, bounded cleanup of Sources that are still soft-deleted (deleted_at set) while no Python purge exists to
 * remove them: rows left by the interim soft-delete behaviour. Safe to run repeatedly and concurrently (a row that is
 * already gone is "missing").
 */
export async function purgeSoftDeletedSources(limit = LEGACY_PURGE_BATCH): Promise<LegacyPurgeSummary> {
  const batch = Math.max(1, Math.min(limit, 100));
  const rows = (await sql`
    SELECT "id", "workspace_id" FROM "files"
     WHERE "deleted_at" IS NOT NULL AND ("purge_next_retry_at" IS NULL OR "purge_next_retry_at" <= now())
     ORDER BY "deleted_at" ASC
     LIMIT ${batch}
  `) as unknown as Array<{ id: number; workspace_id: string }>;
  let purged = 0;
  let blobFailed = 0;
  for (const row of rows) {
    const outcome = await hardDeleteSource({
      workspaceId: row.workspace_id,
      userId: null,
      sourceId: row.id,
      onlySoftDeleted: true,
    });
    if (outcome === "deleted") purged += 1;
    else if (outcome === "blob_failed") blobFailed += 1;
  }
  return { listed: rows.length, purged, blobFailed };
}
