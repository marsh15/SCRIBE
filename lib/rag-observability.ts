import { and, eq } from "drizzle-orm";
import { nanoid } from "nanoid";
import { db } from "@/lib/db-config";
import {
  ragEvaluations,
  ragTraceChunks,
  ragTraces,
  type InsertRagEvaluation,
} from "@/lib/db-schema";
import type { SearchDocumentsResult, SearchDocumentResult } from "@/lib/search";
import {
  RAG_TOOL_RESULT_TYPE,
  type RagToolChunk,
  type RagToolResult,
} from "@/lib/rag-types";

type ChunkMetadata = {
  chunkIndex?: number;
  totalChunks?: number;
  estimatedPage?: number;
  totalPages?: number;
  section?: number;
};

export type RagTraceView = {
  trace: typeof ragTraces.$inferSelect;
  chunks: (typeof ragTraceChunks.$inferSelect)[];
  evaluation: typeof ragEvaluations.$inferSelect | null;
};

export function contentPreview(content: string, maxLength = 360) {
  const normalized = content.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  return `${normalized.slice(0, maxLength - 3).trim()}...`;
}

function chunkIndexFor(result: SearchDocumentResult, fallbackIndex: number) {
  const metadata = result.metadata as ChunkMetadata | null;
  return metadata?.chunkIndex ?? fallbackIndex;
}

function formatLocation(metadata: unknown, chunkIndex: number) {
  const typed = metadata as ChunkMetadata | null;
  return [
    typed?.estimatedPage
      ? `Page ~${typed.estimatedPage}${typed.totalPages ? `/${typed.totalPages}` : ""}`
      : null,
    `Chunk ${chunkIndex + 1}/${typed?.totalChunks ?? "?"}`,
    typed?.section ? `Section ${typed.section}` : null,
  ]
    .filter(Boolean)
    .join(", ");
}

export function buildRagChunks(search: SearchDocumentsResult): RagToolChunk[] {
  return search.documents.map((result, index) => {
    const chunkIndex = chunkIndexFor(result, index);
    return {
      rank: index + 1,
      documentId: result.id,
      fileId: result.file.id,
      fileName: result.file.name,
      fileType: result.file.type,
      chunkIndex,
      similarity: Number(result.similarity),
      metadata: result.metadata,
      contentPreview: contentPreview(result.content),
      content: result.content,
    };
  });
}

export function formatRagContext(chunks: RagToolChunk[]) {
  return chunks
    .map((chunk) => {
      const location = formatLocation(chunk.metadata, chunk.chunkIndex);
      return `[Citation ${chunk.rank}] Source: [${chunk.fileName}](/files/${chunk.fileId}) | ${location}\nContent: ${chunk.content}`;
    })
    .join("\n\n---\n\n");
}

export function buildRagToolResult(
  search: SearchDocumentsResult,
  traceId?: string
): RagToolResult {
  const chunks = buildRagChunks(search);
  const emptyMessage =
    "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first.";

  return {
    type: RAG_TOOL_RESULT_TYPE,
    traceId,
    query: search.query,
    status: chunks.length > 0 ? "retrieved" : "empty",
    message: chunks.length > 0 ? undefined : emptyMessage,
    context: chunks.length > 0 ? formatRagContext(chunks) : emptyMessage,
    timings: search.timings,
    topK: search.topK,
    threshold: search.threshold,
    chunks,
  };
}

export function buildRagTraceChunkRows(traceId: string, chunks: RagToolChunk[]) {
  return chunks.map((chunk) => ({
    traceId,
    documentId: chunk.documentId,
    fileId: chunk.fileId,
    fileName: chunk.fileName,
    chunkIndex: chunk.chunkIndex,
    similarity: chunk.similarity,
    rank: chunk.rank,
    contentPreview: chunk.contentPreview,
    metadata: chunk.metadata,
  }));
}

export async function recordRagTrace(input: {
  chatId: string;
  userId: string;
  userMessageId?: string;
  search: SearchDocumentsResult;
}) {
  const traceId = nanoid();
  const toolResult = buildRagToolResult(input.search, traceId);

  await db.insert(ragTraces).values({
    id: traceId,
    chatId: input.chatId,
    userId: input.userId,
    userMessageId: input.userMessageId,
    query: input.search.query,
    status: toolResult.status,
    embeddingMs: input.search.timings.embeddingMs,
    retrievalMs: input.search.timings.retrievalMs,
    totalMs: input.search.timings.totalMs,
    topK: input.search.topK,
    threshold: input.search.threshold,
  });

  const chunkRows = buildRagTraceChunkRows(traceId, toolResult.chunks);
  if (chunkRows.length > 0) {
    await db.insert(ragTraceChunks).values(chunkRows);
  }

  return toolResult;
}

export async function completeRagTrace(input: {
  traceId: string;
  assistantMessageId: string;
  generationMs: number;
  totalMs: number;
  status?: string;
}) {
  await db
    .update(ragTraces)
    .set({
      assistantMessageId: input.assistantMessageId,
      generationMs: input.generationMs,
      totalMs: input.totalMs,
      status: input.status ?? "completed",
    })
    .where(eq(ragTraces.id, input.traceId));
}

export async function recordRagEvaluation(input: InsertRagEvaluation) {
  await db
    .insert(ragEvaluations)
    .values(input)
    .onConflictDoUpdate({
      target: ragEvaluations.traceId,
      set: {
        status: input.status,
        judgeModel: input.judgeModel,
        groundednessScore: input.groundednessScore,
        answerRelevanceScore: input.answerRelevanceScore,
        citationSupportScore: input.citationSupportScore,
        overallScore: input.overallScore,
        verdict: input.verdict,
        rationale: input.rationale,
        error: input.error,
      },
    });
}

export async function getLatestRagTraceView(input: {
  userId: string;
  chatId?: string;
  assistantMessageId?: string;
}): Promise<RagTraceView | null> {
  if (!input.chatId && !input.assistantMessageId) return null;

  const trace = await db.query.ragTraces.findFirst({
    where: input.assistantMessageId
      ? and(
          eq(ragTraces.userId, input.userId),
          eq(ragTraces.assistantMessageId, input.assistantMessageId)
        )
      : and(eq(ragTraces.userId, input.userId), eq(ragTraces.chatId, input.chatId!)),
    orderBy: (table, { desc }) => [desc(table.createdAt)],
  });

  if (!trace) return null;

  const [chunks, evaluation] = await Promise.all([
    db.query.ragTraceChunks.findMany({
      where: eq(ragTraceChunks.traceId, trace.id),
      orderBy: (table, { asc }) => [asc(table.rank)],
    }),
    db.query.ragEvaluations.findFirst({
      where: eq(ragEvaluations.traceId, trace.id),
      orderBy: (table, { desc }) => [desc(table.createdAt)],
    }),
  ]);

  return {
    trace,
    chunks,
    evaluation: evaluation ?? null,
  };
}
