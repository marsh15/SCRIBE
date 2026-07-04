import { beforeAll, describe, expect, it } from "vitest";
import type { SearchDocumentsResult } from "@/lib/search";

let rag: typeof import("@/lib/rag-observability");

beforeAll(async () => {
  process.env.NEON_DATABASE_URL ??= "postgres://test:test@localhost:5432/test";
  rag = await import("@/lib/rag-observability");
});

describe("RAG trace payload builders", () => {
  const searchResult: SearchDocumentsResult = {
    query: "refund policy",
    timings: { embeddingMs: 10, retrievalMs: 15, totalMs: 25 },
    topK: 10,
    threshold: 0.3,
    documents: [
      {
        id: 42,
        content: "Refunds are available within 30 days when the receipt is present.",
        metadata: {
          chunkIndex: 2,
          totalChunks: 9,
          estimatedPage: 4,
          totalPages: 12,
        },
        file: {
          id: 7,
          name: "policy.pdf",
          type: "application/pdf",
        },
        similarity: 0.88,
      },
    ],
  };

  it("builds a structured tool result with citation context", () => {
    const result = rag.buildRagToolResult(searchResult, "trace-1");

    expect(result).toEqual(
      expect.objectContaining({
        traceId: "trace-1",
        query: "refund policy",
        status: "retrieved",
        topK: 10,
      }),
    );
    expect(result.context).toContain("[Citation 1] Source: [policy.pdf](/files/7)");
    expect(result.chunks[0]).toEqual(
      expect.objectContaining({
        documentId: 42,
        fileId: 7,
        chunkIndex: 2,
        similarity: 0.88,
      }),
    );
  });

  it("builds insert rows for retrieved chunks", () => {
    const result = rag.buildRagToolResult(searchResult, "trace-1");
    const rows = rag.buildRagTraceChunkRows("trace-1", result.chunks);

    expect(rows).toEqual([
      expect.objectContaining({
        traceId: "trace-1",
        documentId: 42,
        fileName: "policy.pdf",
        rank: 1,
        contentPreview:
          "Refunds are available within 30 days when the receipt is present.",
      }),
    ]);
  });
});
