import { NextResponse } from "next/server";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { generateEmbeddings } from "@/lib/embeddings";
import { recordUsageEvent } from "@/lib/billing/usage";
import { getUsageSummary } from "@/lib/billing/usage";
import { withDatabaseRetry } from "@/lib/db-retry";
import {
  MAX_PAYLOAD_BYTES,
  estimateTokens,
  summarizeInsertedChunks,
  type BatchRequestBody,
  validateBatchRequest,
} from "@/lib/ingestion/batch";
import { internalError } from "@/lib/api-errors";

export const maxDuration = 60;

export async function POST(req: Request) {
  try {
    // ---- Auth ----
    const userId = await getUserId();

    // ---- Payload size guard ----
    const contentLength = parseInt(req.headers.get("content-length") || "0", 10);
    if (contentLength > MAX_PAYLOAD_BYTES) {
      return NextResponse.json(
        { error: `Payload too large: ${contentLength} bytes (max ${MAX_PAYLOAD_BYTES}).` },
        { status: 413 }
      );
    }

    // ---- Parse body ----
    const parsed = validateBatchRequest((await req.json()) as BatchRequestBody);
    if (!parsed.ok) {
      return NextResponse.json({ error: parsed.error }, { status: 400 });
    }
    const { fileId, batchIndex, chunks } = parsed.value;

    // ---- File ownership check ----
    const file = await withDatabaseRetry("batchLoadFile", () =>
      db.query.files.findFirst({
        where: eq(files.id, fileId),
      })
    );

    if (!file) {
      return NextResponse.json({ error: "File not found." }, { status: 404 });
    }

    if (file.userId !== userId) {
      return NextResponse.json(
        { error: "You do not own this file." },
        { status: 403 }
      );
    }

    if (file.status !== "processing") {
      return NextResponse.json(
        { error: `File is in '${file.status}' state, expected 'processing'.` },
        { status: 409 }
      );
    }

    // ---- Quota check ----
    const usage = await getUsageSummary(userId);
    if (!usage.allowOverage && usage.projectedOverageInr > 0) {
      return NextResponse.json(
        { error: "Embedding token quota exceeded. Upgrade your plan to continue." },
        { status: 402 }
      );
    }

    // ---- Generate embeddings (single batchEmbedContents call) ----
    const chunkTexts = chunks.map((chunk) => chunk.content);
    const embeddings = await generateEmbeddings(chunkTexts);

    // ---- Insert document rows idempotently ----
    const insertedRows = await withDatabaseRetry("batchInsertDocuments", () =>
      db
        .insert(documents)
        .values(
          chunks.map((chunk, index) => ({
            fileId,
            chunkIndex: chunk.metadata.chunkIndex,
            content: chunk.content,
            metadata: chunk.metadata,
            embeddings: embeddings[index],
          }))
        )
        .onConflictDoNothing({
          target: [documents.fileId, documents.chunkIndex],
        })
        .returning({
          chunkIndex: documents.chunkIndex,
        })
    );

    const insertedSummary = summarizeInsertedChunks(
      chunks,
      insertedRows.map((row) => row.chunkIndex)
    );

    // ---- Record usage for newly inserted chunks only ----
    if (insertedSummary.insertedChunkCount > 0) {
      await recordUsageEvent({
        userId,
        metric: "embedding_input_tokens",
        quantity: estimateTokens(insertedSummary.insertedText),
        unit: "tokens",
        sourceType: "ingest",
        sourceId: String(fileId),
        isEstimated: true,
      });
    }

    return NextResponse.json({
      ok: true,
      batchIndex,
      processedChunks: insertedSummary.insertedChunkCount,
      replayedChunks: chunks.length - insertedSummary.insertedChunkCount,
    });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    return internalError("batch-ingest", error);
  }
}
