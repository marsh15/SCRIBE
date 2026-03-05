import { and, eq, isNull, lte, or } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { documents, files, ingestionJobs } from "@/lib/db-schema";
import { chunkContent } from "@/lib/chunking";
import { generateEmbeddings } from "@/lib/embeddings";
import { extractTextFromBuffer } from "@/lib/ingestion/extract";
import { downloadBlobToBuffer } from "@/lib/storage/blob";
import { recordUsageEvent } from "@/lib/billing/usage";

const MAX_ATTEMPTS = 5;

function fileDataUriToBuffer(dataUri: string) {
  const match = dataUri.match(/^data:(.+);base64,(.+)$/);
  if (!match) throw new Error("Invalid data URI content");
  return Buffer.from(match[2], "base64");
}

function estimateTokens(text: string) {
  return Math.ceil(text.length / 4);
}

async function processSingleJob(jobId: number) {
  const job = await db.query.ingestionJobs.findFirst({
    where: eq(ingestionJobs.id, jobId),
  });
  if (!job) return { processed: false, reason: "job_not_found" };

  const [updated] = await db
    .update(ingestionJobs)
    .set({
      status: "processing",
      attempts: job.attempts + 1,
      startedAt: new Date(),
      updatedAt: new Date(),
      lastError: null,
    })
    .where(eq(ingestionJobs.id, job.id))
    .returning();

  try {
    const file = await db.query.files.findFirst({
      where: eq(files.id, updated.fileId),
    });

    if (!file) throw new Error("File not found for ingestion job");

    await db
      .update(files)
      .set({ status: "processing", processingError: null })
      .where(eq(files.id, file.id));

    let bytes: ArrayBuffer | Uint8Array;
    if (file.storageUrl) {
      bytes = await downloadBlobToBuffer(file.storageUrl);
    } else if (file.fileData) {
      bytes = fileDataUriToBuffer(file.fileData);
    } else {
      throw new Error("No source payload available for ingestion");
    }

    const buffer = bytes instanceof ArrayBuffer ? Buffer.from(new Uint8Array(bytes)) : Buffer.from(bytes);
    const extraction = await extractTextFromBuffer({
      buffer,
      fileName: file.name,
      mimeType: file.type,
    });

    const chunks = await chunkContent(extraction.extractedText, {
      fileName: file.name,
      numPages: extraction.numPages,
    });

    const chunkTexts = chunks.map((chunk) => chunk.content);
    const embeddings = await generateEmbeddings(chunkTexts);

    await db.delete(documents).where(eq(documents.fileId, file.id));

    if (chunks.length > 0) {
      await db.insert(documents).values(
        chunks.map((chunk, index) => ({
          fileId: file.id,
          content: chunk.content,
          metadata: chunk.metadata,
          embeddings: embeddings[index],
        }))
      );
    }

    await db
      .update(files)
      .set({
        extractedText: extraction.extractedText,
        textBytes: extraction.extractedText.length,
        status: "ready",
        processingError: null,
      })
      .where(eq(files.id, file.id));

    await db
      .update(ingestionJobs)
      .set({
        status: "completed",
        finishedAt: new Date(),
        updatedAt: new Date(),
      })
      .where(eq(ingestionJobs.id, updated.id));

    if (file.userId) {
      await recordUsageEvent({
        userId: file.userId,
        metric: "embedding_input_tokens",
        quantity: estimateTokens(extraction.extractedText),
        unit: "tokens",
        sourceType: "ingest",
        sourceId: String(file.id),
        isEstimated: true,
      });
    }

    return {
      processed: true,
      fileId: file.id,
      chunks: chunks.length,
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : "Unknown ingestion error";

    const nextAttempts = (job.attempts ?? 0) + 1;
    const shouldRetry = nextAttempts < MAX_ATTEMPTS;
    const waitMinutes = Math.min(60, 2 ** nextAttempts);
    const nextRetryAt = shouldRetry
      ? new Date(Date.now() + waitMinutes * 60 * 1000)
      : null;

    await db
      .update(ingestionJobs)
      .set({
        status: shouldRetry ? "queued" : "failed",
        lastError: errorMessage,
        nextRetryAt,
        finishedAt: shouldRetry ? null : new Date(),
        updatedAt: new Date(),
      })
      .where(eq(ingestionJobs.id, updated.id));

    await db
      .update(files)
      .set({
        status: "failed",
        processingError: errorMessage,
      })
      .where(eq(files.id, updated.fileId));

    return {
      processed: false,
      reason: errorMessage,
      willRetry: shouldRetry,
    };
  }
}

export async function processQueuedIngestionJobs(limit = 2) {
  const now = new Date();

  const queuedJobs = await db.query.ingestionJobs.findMany({
    where: and(
      eq(ingestionJobs.status, "queued"),
      or(lte(ingestionJobs.nextRetryAt, now), isNull(ingestionJobs.nextRetryAt))
    ),
    orderBy: (table, { asc }) => [asc(table.createdAt)],
    limit,
  });

  const results = [];
  for (const job of queuedJobs) {
    results.push(await processSingleJob(job.id));
  }

  return {
    processedJobs: results.length,
    results,
  };
}
