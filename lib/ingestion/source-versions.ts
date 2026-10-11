import { generateClientTokenFromReadWriteToken } from "@vercel/blob/client";
import { createHash } from "node:crypto";
import { sql } from "@/lib/db-config";
import { getUsageSummary, recordUsageEvent } from "@/lib/billing/usage";
import { getUserMaxUploadBytes } from "@/lib/uploads/limits";
import { deleteBlob, downloadBlobToBuffer } from "@/lib/storage/blob";
import { requireEnv } from "@/lib/env";
import { ensurePersonalWorkspace } from "@/lib/workspace";
import {
  parseSourceUploadMetadata,
  SourceIntakeError,
  sourceVersionUploadPath,
  validateSourceUploadAllowance,
  type CompletedSourceBlob,
  type SourceUploadCompletionPayload,
  type SourceUploadMetadata,
} from "@/lib/ingestion/source-intake-policy";
import {
  completeReservedVersionUpload,
  type CompleteOutcome,
  type ReservedVersion,
} from "@/lib/ingestion/source-version-policy";
import {
  toVersionView,
  type SourceVersionRow,
  type SourceVersionView,
} from "@/lib/ingestion/source-version-state";

export interface VersionReservation {
  source: { id: number };
  version: { id: string; versionNumber: number };
  uploadPath: string;
  clientToken: string;
}

/** Postgres raises the codes below from scribe_reserve_source_version (drizzle/0015). */
function mapReservationError(error: unknown): never {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("SOURCE_NOT_FOUND")) {
    throw new SourceIntakeError("Source not found", 404, "source_not_found");
  }
  if (message.includes("NO_ACTIVE_VERSION")) {
    throw new SourceIntakeError(
      "This Source has no searchable version yet. Wait for indexing to finish or retry it first.",
      409,
      "no_active_version"
    );
  }
  if (message.includes("TYPE_MISMATCH")) {
    throw new SourceIntakeError(
      "A new version must be the same file type as the Source.",
      415,
      "type_mismatch"
    );
  }
  throw error;
}

/**
 * Reserve version N+1 of a Source and hand the browser a client token to upload it. The previous active version is
 * untouched and stays searchable; nothing is queued until the upload-complete callback stores and hashes the bytes.
 */
export async function reserveSourceVersion(input: {
  userId: string;
  sourceId: number;
  metadata: SourceUploadMetadata;
  callbackUrl: string;
}): Promise<VersionReservation> {
  if (!Number.isInteger(input.sourceId) || input.sourceId <= 0) {
    throw new SourceIntakeError("Invalid Source id", 400, "invalid_source");
  }
  const metadata = parseSourceUploadMetadata(input.metadata);
  const blobToken = requireEnv("BLOB_READ_WRITE_TOKEN");
  const [usage, uploadLimit] = await Promise.all([
    getUsageSummary(input.userId),
    getUserMaxUploadBytes(input.userId),
  ]);
  validateSourceUploadAllowance(metadata, usage, uploadLimit);
  const workspaceId = await ensurePersonalWorkspace(input.userId);

  let reserved: Array<{ out_version_id: string; out_version_number: number }>;
  try {
    reserved = (await sql`
      SELECT * FROM scribe_reserve_source_version(
        ${workspaceId}::uuid, ${input.sourceId}::int, ${input.userId}, ${metadata.type}, ${metadata.size}::bigint)
    `) as typeof reserved;
  } catch (error) {
    mapReservationError(error);
  }
  const row = reserved[0];
  if (!row) throw new Error("Version reservation returned no row");
  const versionId = row.out_version_id;
  const versionNumber = Number(row.out_version_number);

  const uploadPath = sourceVersionUploadPath(input.userId, input.sourceId, versionNumber, metadata.name);
  try {
    const clientToken = await generateClientTokenFromReadWriteToken({
      token: blobToken,
      pathname: uploadPath,
      allowedContentTypes: [metadata.type],
      maximumSizeInBytes: metadata.size,
      addRandomSuffix: false,
      validUntil: Date.now() + 15 * 60 * 1000,
      onUploadCompleted: {
        callbackUrl: input.callbackUrl,
        tokenPayload: JSON.stringify({
          sourceId: input.sourceId,
          userId: input.userId,
          versionId,
        } satisfies SourceUploadCompletionPayload),
      },
    });
    return { source: { id: input.sourceId }, version: { id: versionId, versionNumber }, uploadPath, clientToken };
  } catch (error) {
    // the reservation never reached the browser: close it so the history does not show a phantom upload
    await sql`
      UPDATE "source_versions" SET "upload_state" = 'failed'
      WHERE "id" = ${versionId}::uuid AND "workspace_id" = ${workspaceId}::uuid AND "upload_state" = 'uploading'
    `;
    throw error;
  }
}

async function sha256OfBlob(url: string) {
  const bytes = await downloadBlobToBuffer(url);
  return createHash("sha256").update(Buffer.from(bytes)).digest("hex");
}

/** Blob callback for a reserved version (see completeReservedVersionUpload for the idempotency contract). */
export async function completeSourceVersionUpload(input: {
  payload: SourceUploadCompletionPayload & { versionId: string };
  blob: CompletedSourceBlob;
}) {
  const workspaceId = await ensurePersonalWorkspace(input.payload.userId);
  return completeReservedVersionUpload(
    {
      versionId: input.payload.versionId,
      sourceId: input.payload.sourceId,
      userId: input.payload.userId,
      blob: input.blob,
    },
    {
      loadVersion: async (versionId): Promise<ReservedVersion | null> => {
        const rows = (await sql`
          SELECT v."id", v."source_id", v."version_number", v."size_bytes", v."mime_type", v."upload_state"
          FROM "source_versions" v
          INNER JOIN "files" f ON f."id" = v."source_id"
          WHERE v."id" = ${versionId}::uuid AND v."workspace_id" = ${workspaceId}::uuid
            AND f."user_id" = ${input.payload.userId} AND f."deleted_at" IS NULL
        `) as Array<{
          id: string;
          source_id: number;
          version_number: number;
          size_bytes: string | number | null;
          mime_type: string | null;
          upload_state: string;
        }>;
        const row = rows[0];
        if (!row) return null;
        return {
          id: row.id,
          sourceId: Number(row.source_id),
          versionNumber: Number(row.version_number),
          sizeBytes: row.size_bytes === null ? null : Number(row.size_bytes),
          mimeType: row.mime_type,
          uploadState: row.upload_state,
        };
      },
      hashBlob: sha256OfBlob,
      complete: async ({ versionId, url, sha256, size }) => {
        const rows = (await sql`
          SELECT * FROM scribe_complete_source_version(
            ${workspaceId}::uuid, ${versionId}::uuid, ${url}, ${sha256}, ${size}::bigint)
        `) as Array<{
          outcome: CompleteOutcome;
          out_version_number: number | null;
          out_duplicate_of: string | null;
        }>;
        const row = rows[0];
        return {
          outcome: row?.outcome ?? "not_found",
          versionNumber: row?.out_version_number ?? null,
          duplicateOf: row?.out_duplicate_of ?? null,
        };
      },
      deleteBlob,
      recordStorageUsage: async (version) => {
        await recordUsageEvent({
          userId: input.payload.userId,
          metric: "storage_gb_day",
          quantity: Math.ceil(((version.sizeBytes ?? 0) / (1024 * 1024 * 1024)) * 1000),
          unit: "gb_day",
          sourceType: "upload",
          sourceId: String(version.sourceId),
          isEstimated: true,
          idempotencyKey: `source:${version.sourceId}:version:${version.id}:storage`,
        });
      },
    }
  );
}

/** Version history of one Source, newest first. A foreign or deleted Source yields an empty list. */
export async function listSourceVersions(
  workspaceId: string,
  userId: string,
  sourceId: number
): Promise<SourceVersionView[]> {
  const rows = (await sql`
    SELECT v."id", v."version_number", v."size_bytes", v."created_at", v."upload_state", v."duplicate_of",
           d."version_number" AS duplicate_of_number,
           (f."active_version_id" = v."id") AS is_active, (f."desired_version_id" = v."id") AS is_desired,
           g."status" AS generation_status,
           j."status" AS job_status, j."stage" AS job_stage, j."attempts" AS job_attempts,
           j."error_code", j."last_error"
    FROM "source_versions" v
    INNER JOIN "files" f ON f."id" = v."source_id"
    LEFT JOIN "source_versions" d ON d."id" = v."duplicate_of"
    LEFT JOIN LATERAL (
      SELECT "status" FROM "index_generations" WHERE "source_version_id" = v."id" ORDER BY "created_at" DESC LIMIT 1
    ) g ON true
    LEFT JOIN LATERAL (
      SELECT "status", "stage", "attempts", "error_code", "last_error" FROM "ingestion_jobs"
      WHERE "source_version_id" = v."id" ORDER BY "updated_at" DESC, "id" DESC LIMIT 1
    ) j ON true
    WHERE v."source_id" = ${sourceId}::int AND v."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
    ORDER BY v."version_number" DESC
  `) as Array<Record<string, unknown>>;

  return rows.map((r) =>
    toVersionView({
      id: String(r.id),
      versionNumber: Number(r.version_number),
      sizeBytes: r.size_bytes === null || r.size_bytes === undefined ? null : Number(r.size_bytes),
      createdAt: r.created_at instanceof Date ? r.created_at.toISOString() : String(r.created_at),
      uploadState: r.upload_state as SourceVersionRow["uploadState"],
      duplicateOf: (r.duplicate_of as string | null) ?? null,
      duplicateOfNumber: r.duplicate_of_number === null || r.duplicate_of_number === undefined ? null : Number(r.duplicate_of_number),
      isActive: r.is_active === true,
      isDesired: r.is_desired === true,
      generationStatus: (r.generation_status as string | null) ?? null,
      jobStatus: (r.job_status as string | null) ?? null,
      jobStage: (r.job_stage as string | null) ?? null,
      jobAttempts: Number(r.job_attempts ?? 0),
      errorCode: (r.error_code as string | null) ?? null,
      lastError: (r.last_error as string | null) ?? null,
    })
  );
}
