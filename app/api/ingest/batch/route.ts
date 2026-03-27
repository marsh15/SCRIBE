import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth";
import { db } from "@/lib/db-config";
import { files, documents } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { generateEmbeddings } from "@/lib/embeddings";
import { recordUsageEvent } from "@/lib/billing/usage";
import { getUsageSummary } from "@/lib/billing/usage";
import { withDatabaseRetry } from "@/lib/db-retry";

// ---- Validation constants ----
const MAX_CHUNKS_PER_BATCH = 100;
const MAX_CHUNK_CHARS = 5000;
const MAX_PAYLOAD_BYTES = 2 * 1024 * 1024; // 2MB

/** Estimate tokens from text (same formula as worker.ts) */
function estimateTokens(text: string) {
  return Math.ceil(text.length / 4);
}

interface BatchChunk {
  content: string;
  metadata: Record<string, unknown>;
}

interface BatchRequestBody {
  fileId: number;
  batchIndex: number;
  totalBatches: number;
  chunks: BatchChunk[];
}

/**
 * Validate a batch of chunks. Returns an error message string or null if valid.
 */
function validateBatch(
  chunks: BatchChunk[]
): string | null {
  if (!Array.isArray(chunks) || chunks.length === 0) {
    return "Chunks array is empty or not an array.";
  }

  if (chunks.length > MAX_CHUNKS_PER_BATCH) {
    return `Too many chunks: ${chunks.length}. Maximum is ${MAX_CHUNKS_PER_BATCH}.`;
  }

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (!chunk.content || typeof chunk.content !== "string") {
      return `Chunk ${i} has invalid or missing content.`;
    }
    if (chunk.content.length > MAX_CHUNK_CHARS) {
      return `Chunk ${i} exceeds max length: ${chunk.content.length} chars (max ${MAX_CHUNK_CHARS}).`;
    }
  }

  return null;
}

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
    const body: BatchRequestBody = await req.json();
    const { fileId, batchIndex, totalBatches, chunks } = body;

    if (!fileId || batchIndex == null || !totalBatches || !chunks) {
      return NextResponse.json(
        { error: "Missing required fields: fileId, batchIndex, totalBatches, chunks." },
        { status: 400 }
      );
    }

    // ---- Validate chunks ----
    const validationError = validateBatch(chunks);
    if (validationError) {
      return NextResponse.json({ error: validationError }, { status: 400 });
    }

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
    const chunkTexts = chunks.map((c) => c.content);
    const embeddings = await generateEmbeddings(chunkTexts);

    // ---- Insert document rows ----
    await withDatabaseRetry("batchInsertDocuments", () =>
      db.insert(documents).values(
        chunks.map((chunk, index) => ({
          fileId,
          content: chunk.content,
          metadata: chunk.metadata,
          embeddings: embeddings[index],
        }))
      )
    );

    // ---- Record usage ----
    const totalChars = chunkTexts.reduce((sum, t) => sum + t.length, 0);
    await recordUsageEvent({
      userId,
      metric: "embedding_input_tokens",
      quantity: estimateTokens(totalChars.toString().length > 0 ? chunkTexts.join("") : ""),
      unit: "tokens",
      sourceType: "ingest",
      sourceId: String(fileId),
      isEstimated: true,
    });

    return NextResponse.json({
      ok: true,
      batchIndex,
      processedChunks: chunks.length,
    });
  } catch (error) {
    console.error("Batch ingest error:", error);
    const message =
      error instanceof Error ? error.message : "Batch ingestion failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
