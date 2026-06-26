import { generateClientTokenFromReadWriteToken } from "@vercel/blob/client";
import { and, eq } from "drizzle-orm";
import { db, sql } from "@/lib/db-config";
import { documents, files, ingestionJobs, type SourceStatus } from "@/lib/db-schema";
import { getUsageSummary, recordUsageEvent } from "@/lib/billing/usage";
import { getUserMaxUploadBytes } from "@/lib/uploads/limits";
import { extractTextFromBuffer } from "@/lib/ingestion/extract";
import { chunkContent } from "@/lib/chunking";
import { generateEmbeddings } from "@/lib/embeddings";
import { deleteBlob, downloadBlobToBuffer } from "@/lib/storage/blob";
import { requireEnv } from "@/lib/env";
import {
  completeReservedSourceUpload,
  deleteOwnedSource,
  parseSourceUploadMetadata,
  retryDelayMinutes,
  shouldRetrySource,
  sourceUploadPath,
  SourceIntakeError,
  validateSourceUploadAllowance,
  type CompletedSourceBlob,
  type SourceUploadMetadata,
} from "@/lib/ingestion/source-intake-policy";

export {
  completeReservedSourceUpload,
  deleteOwnedSource,
  parseSourceUploadMetadata,
  retryDelayMinutes,
  shouldRetrySource,
  SourceIntakeError,
  validateSourceUploadAllowance,
  validateCompletedSourceBlob,
} from "@/lib/ingestion/source-intake-policy";
export type {
  CompletedSourceBlob,
  SourceUploadMetadata,
} from "@/lib/ingestion/source-intake-policy";

const PROCESSING_STALE_MINUTES = 10;
const UPLOAD_STALE_HOURS = 24;

export interface SourceSummary {
  id: number;
  name: string;
  size: number;
  type: string;
  status: SourceStatus;
  processingError: string | null;
}

export interface SourceUploadReservation {
  source: SourceSummary;
  uploadPath: string;
  clientToken: string;
}

export interface SourceUploadCompletionPayload {
  sourceId: number;
  userId: string;
}

export async function reserveSourceUpload(input: {
  userId: string;
  metadata: SourceUploadMetadata;
  callbackUrl: string;
}): Promise<SourceUploadReservation> {
  const metadata = parseSourceUploadMetadata(input.metadata);
  const blobToken = requireEnv("BLOB_READ_WRITE_TOKEN");

  const [usage, uploadLimit] = await Promise.all([
    getUsageSummary(input.userId),
    getUserMaxUploadBytes(input.userId),
  ]);

  validateSourceUploadAllowance(metadata, usage, uploadLimit);

  const [source] = await db
    .insert(files)
    .values({
      name: metadata.name,
      size: metadata.size,
      type: metadata.type,
      userId: input.userId,
      status: "uploading",
      updatedAt: new Date(),
    })
    .returning();

  const uploadPath = sourceUploadPath(input.userId, source.id, metadata.name);

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
          sourceId: source.id,
          userId: input.userId,
        } satisfies SourceUploadCompletionPayload),
      },
    });

    return {
      source: {
        id: source.id,
        name: source.name,
        size: source.size,
        type: source.type,
        status: source.status,
        processingError: source.processingError,
      },
      uploadPath,
      clientToken,
    };
  } catch (error) {
    await db
      .update(files)
      .set({
        status: "failed",
        processingError: "Could not prepare private upload",
        updatedAt: new Date(),
      })
      .where(eq(files.id, source.id));
    throw error;
  }
}

export function parseSourceUploadCompletionPayload(payload: string | null | undefined) {
  try {
    const parsed = JSON.parse(payload ?? "") as Partial<SourceUploadCompletionPayload>;
    if (
      !Number.isInteger(parsed.sourceId) ||
      !parsed.sourceId ||
      parsed.sourceId <= 0 ||
      typeof parsed.userId !== "string" ||
      parsed.userId.length === 0
    ) {
      throw new Error("Invalid completion payload");
    }
    return parsed as SourceUploadCompletionPayload;
  } catch {
    throw new SourceIntakeError(
      "Invalid upload completion payload",
      400,
      "invalid_completion"
    );
  }
}

export async function completeSourceUpload(input: {
  payload: SourceUploadCompletionPayload;
  blob: CompletedSourceBlob;
}) {
  const result = await completeReservedSourceUpload(
    {
      sourceId: input.payload.sourceId,
      userId: input.payload.userId,
      blob: input.blob,
    },
    {
      loadSource: async (sourceId, userId) =>
        (await db.query.files.findFirst({
          where: and(eq(files.id, sourceId), eq(files.userId, userId)),
        })) ?? null,
      deleteBlob,
      transitionToQueued: async (source, blob) => {
        const transitioned = await sql`
          WITH source AS (
            UPDATE "files"
            SET
              "storage_key" = ${blob.pathname},
              "storage_url" = ${blob.url},
              "status" = 'queued',
              "processing_error" = NULL,
              "updated_at" = now()
            WHERE "id" = ${source.id}
              AND "user_id" = ${source.userId}
              AND "status" = 'uploading'
            RETURNING "id"
          )
          INSERT INTO "ingestion_jobs" ("file_id", "status", "attempts", "created_at", "updated_at")
          SELECT "id", 'queued', 0, now(), now() FROM source
          ON CONFLICT ("file_id") DO NOTHING
          RETURNING "file_id"
        `;
        return transitioned.length > 0;
      },
      recordStorageUsage: async (source) => {
        await recordUsageEvent({
          userId: source.userId,
          metric: "storage_gb_day",
          quantity: Math.ceil((source.size / (1024 * 1024 * 1024)) * 1000),
          unit: "gb_day",
          sourceType: "upload",
          sourceId: String(source.id),
          isEstimated: true,
          idempotencyKey: `source:${source.id}:storage`,
        });
      },
    }
  );

  if (result.queued) {
    console.info("[SourceIntake] queued", { sourceId: input.payload.sourceId });
  }
  return result;
}

function dataUriToBuffer(dataUri: string) {
  const match = dataUri.match(/^data:(.+);base64,(.+)$/);
  if (!match) throw new Error("Invalid legacy Source data");
  return Buffer.from(match[2], "base64");
}

async function processClaimedSource(job: {
  id: number;
  fileId: number;
  attempts: number;
}) {
  const source = await db.query.files.findFirst({ where: eq(files.id, job.fileId) });
  if (!source) {
    console.warn("[SourceIntake] claimed Source no longer exists", {
      sourceId: job.fileId,
      jobId: job.id,
    });
    return { sourceId: job.fileId, status: "failed" as const, error: "Source not found" };
  }

  try {
    const bytes = source.storageUrl
      ? await downloadBlobToBuffer(source.storageUrl)
      : source.fileData
        ? dataUriToBuffer(source.fileData)
        : null;
    if (!bytes) throw new Error("Source has no original payload");

    const sourceBuffer = bytes instanceof ArrayBuffer
      ? Buffer.from(new Uint8Array(bytes))
      : Buffer.from(bytes);
    const extraction = await extractTextFromBuffer({
      buffer: sourceBuffer,
      fileName: source.name,
      mimeType: source.type,
    });
    const chunks = await chunkContent(extraction.extractedText, {
      fileName: source.name,
      numPages: extraction.numPages,
    });
    const embeddings = await generateEmbeddings(chunks.map((chunk) => chunk.content));

    const queries: any[] = [
      db.delete(documents).where(eq(documents.fileId, source.id)),
    ];
    if (chunks.length > 0) {
      queries.push(
        db.insert(documents).values(
          chunks.map((chunk, index) => ({
            fileId: source.id,
            chunkIndex: chunk.metadata.chunkIndex,
            content: chunk.content,
            metadata: chunk.metadata,
            embeddings: embeddings[index],
          }))
        )
      );
    }
    queries.push(
      db
        .update(files)
        .set({
          extractedText: extraction.extractedText,
          textBytes: extraction.extractedText.length,
          status: "ready",
          processingError: null,
          updatedAt: new Date(),
        })
        .where(eq(files.id, source.id)),
      db
        .update(ingestionJobs)
        .set({
          status: "completed",
          lastError: null,
          nextRetryAt: null,
          finishedAt: new Date(),
          updatedAt: new Date(),
        })
        .where(eq(ingestionJobs.id, job.id))
    );
    await db.batch(queries as [any]);

    await recordUsageEvent({
      userId: source.userId,
      metric: "embedding_input_tokens",
      quantity: Math.ceil(extraction.extractedText.length / 4),
      unit: "tokens",
      sourceType: "ingest",
      sourceId: String(source.id),
      isEstimated: true,
      idempotencyKey: `source:${source.id}:embedding:v1`,
    });

    console.info("[SourceIntake] ready", { sourceId: source.id, chunks: chunks.length });
    return { sourceId: source.id, status: "ready" as const, chunks: chunks.length };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown Source intake error";
    const retrying = shouldRetrySource(job.attempts);
    const nextRetryAt = retrying
      ? new Date(Date.now() + retryDelayMinutes(job.attempts) * 60 * 1000)
      : null;

    await db.batch([
      db
        .update(files)
        .set({
          status: retrying ? "retrying" : "failed",
          processingError: message,
          updatedAt: new Date(),
        })
        .where(eq(files.id, source.id)),
      db
        .update(ingestionJobs)
        .set({
          status: retrying ? "queued" : "failed",
          lastError: message,
          nextRetryAt,
          finishedAt: retrying ? null : new Date(),
          updatedAt: new Date(),
        })
        .where(eq(ingestionJobs.id, job.id)),
    ]);

    console.error("[SourceIntake] processing failed", {
      sourceId: source.id,
      attempts: job.attempts,
      retrying,
      error: message,
    });
    return {
      sourceId: source.id,
      status: retrying ? ("retrying" as const) : ("failed" as const),
      error: message,
      nextRetryAt,
    };
  }
}

export async function runQueuedSourceIntake(limit = 1) {
  const safeLimit = Math.max(1, Math.min(limit, 5));

  const staleUploads = await sql`
    UPDATE "files"
    SET
      "status" = 'failed',
      "processing_error" = 'Upload did not complete within 24 hours',
      "updated_at" = now()
    WHERE "status" = 'uploading'
      AND "updated_at" <= now() - (${UPLOAD_STALE_HOURS} * interval '1 hour')
    RETURNING "id"
  `;

  const abandonedLegacySources = await sql`
    UPDATE "files" AS source
    SET
      "status" = 'failed',
      "processing_error" = 'Legacy browser intake did not complete within 24 hours',
      "updated_at" = now()
    WHERE source."status" = 'processing'
      AND source."storage_url" IS NULL
      AND source."file_data" IS NULL
      AND source."updated_at" <= now() - (${UPLOAD_STALE_HOURS} * interval '1 hour')
      AND NOT EXISTS (
        SELECT 1 FROM "ingestion_jobs" AS job WHERE job."file_id" = source."id"
      )
    RETURNING source."id"
  `;

  const recovered = await sql`
    WITH recovered_jobs AS (
      UPDATE "ingestion_jobs"
      SET
        "status" = 'queued',
        "next_retry_at" = now(),
        "last_error" = 'Recovered after worker timeout',
        "updated_at" = now()
      WHERE "status" = 'processing'
        AND "started_at" <= now() - (${PROCESSING_STALE_MINUTES} * interval '1 minute')
      RETURNING "file_id"
    )
    UPDATE "files"
    SET
      "status" = 'retrying',
      "processing_error" = 'Recovered after worker timeout',
      "updated_at" = now()
    WHERE "id" IN (SELECT "file_id" FROM recovered_jobs)
    RETURNING "id"
  `;

  const claimed = await sql`
    WITH candidates AS (
      SELECT "id"
      FROM "ingestion_jobs"
      WHERE "status" = 'queued'
        AND ("next_retry_at" IS NULL OR "next_retry_at" <= now())
      ORDER BY "created_at" ASC
      FOR UPDATE SKIP LOCKED
      LIMIT ${safeLimit}
    )
    UPDATE "ingestion_jobs" AS jobs
    SET
      "status" = 'processing',
      "attempts" = jobs."attempts" + 1,
      "started_at" = now(),
      "finished_at" = NULL,
      "last_error" = NULL,
      "updated_at" = now()
    FROM candidates
    WHERE jobs."id" = candidates."id"
    RETURNING jobs."id", jobs."file_id", jobs."attempts"
  ` as Array<{ id: number; file_id: number; attempts: number }>;

  const results = [];
  for (const job of claimed) {
    await db
      .update(files)
      .set({ status: "processing", processingError: null, updatedAt: new Date() })
      .where(eq(files.id, job.file_id));
    results.push(
      await processClaimedSource({
        id: job.id,
        fileId: job.file_id,
        attempts: job.attempts,
      })
    );
  }

  return {
    claimed: claimed.length,
    ready: results.filter((result) => result.status === "ready").length,
    retrying: results.filter((result) => result.status === "retrying").length,
    failed: results.filter((result) => result.status === "failed").length,
    recovered: recovered.length,
    staleUploadsFailed: staleUploads.length,
    abandonedLegacySourcesFailed: abandonedLegacySources.length,
    results,
  };
}

export async function deleteSource(userId: string, sourceId: number) {
  const deleted = await deleteOwnedSource(userId, sourceId, {
    loadSource: async (id, ownerId) => {
      const source = await db.query.files.findFirst({
        where: and(eq(files.id, id), eq(files.userId, ownerId)),
      });
      if (!source) return null;
      return {
        ...source,
        storageUrl: source.storageUrl ?? source.storageKey,
      };
    },
    deleteBlob,
    deleteRecord: async (id, ownerId) => {
      await db
        .delete(files)
        .where(and(eq(files.id, id), eq(files.userId, ownerId)));
    },
  });
  if (deleted) console.info("[SourceIntake] deleted", { sourceId });
  return deleted;
}
