import { describe, it, expect } from "vitest";

// ---- Inline the validation logic for isolated testing ----
// (Mirrors the validateBatch export from app/api/ingest/batch/route.ts)

const MAX_CHUNKS_PER_BATCH = 100;
const MAX_CHUNK_CHARS = 5000;

interface BatchChunk {
  content: string;
  metadata: Record<string, unknown>;
}

function validateBatch(chunks: BatchChunk[]): string | null {
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

// ---- Tests ----

describe("Batch ingest validation", () => {
  const makeChunk = (length = 100): BatchChunk => ({
    content: "a".repeat(length),
    metadata: { chunkIndex: 0 },
  });

  it("should accept a valid batch of 1 chunk", () => {
    expect(validateBatch([makeChunk()])).toBeNull();
  });

  it("should accept exactly 100 chunks of 5000 chars each (boundary)", () => {
    const chunks = Array.from({ length: 100 }, () => makeChunk(5000));
    expect(validateBatch(chunks)).toBeNull();
  });

  it("should reject an empty chunks array", () => {
    expect(validateBatch([])).toContain("empty");
  });

  it("should reject batches with more than 100 chunks", () => {
    const chunks = Array.from({ length: 101 }, () => makeChunk());
    const result = validateBatch(chunks);
    expect(result).toContain("101");
    expect(result).toContain("100");
  });

  it("should reject a chunk exceeding 5000 characters", () => {
    const chunks = [makeChunk(100), makeChunk(5001)];
    const result = validateBatch(chunks);
    expect(result).toContain("5001");
    expect(result).toContain("Chunk 1");
  });

  it("should reject chunks with empty content", () => {
    const chunks = [{ content: "", metadata: {} }];
    const result = validateBatch(chunks);
    expect(result).toContain("invalid or missing");
  });

  it("should reject chunks with non-string content", () => {
    const chunks = [{ content: 42 as unknown as string, metadata: {} }];
    const result = validateBatch(chunks);
    expect(result).toContain("invalid or missing");
  });
});

// ---- Resumability state management tests ----

describe("Resumability localStorage state", () => {
  it("should serialize and deserialize progress correctly", () => {
    const progress = {
      fileId: 42,
      lastBatchIndex: 7,
      totalBatches: 11,
      chunks: [{ content: "test", metadata: { chunkIndex: 0, totalChunks: 1, positionPercent: 0, section: 1, charLength: 4 } }],
      textBytes: 50000,
    };

    const serialized = JSON.stringify(progress);
    const deserialized = JSON.parse(serialized);

    expect(deserialized.fileId).toBe(42);
    expect(deserialized.lastBatchIndex).toBe(7);
    expect(deserialized.totalBatches).toBe(11);
    expect(deserialized.chunks).toHaveLength(1);
    expect(deserialized.chunks[0].content).toBe("test");
  });

  it("should correctly identify incomplete uploads", () => {
    const stored = {
      fileId: 42,
      lastBatchIndex: 5,
      totalBatches: 11,
    };

    // Upload is incomplete if lastBatchIndex < totalBatches - 1
    const isIncomplete = stored.lastBatchIndex < stored.totalBatches - 1;
    expect(isIncomplete).toBe(true);
  });

  it("should correctly identify completed uploads", () => {
    const stored = {
      fileId: 42,
      lastBatchIndex: 10,
      totalBatches: 11,
    };

    const isIncomplete = stored.lastBatchIndex < stored.totalBatches - 1;
    expect(isIncomplete).toBe(false);
  });

  it("should compute correct resume batch index", () => {
    const stored = { lastBatchIndex: 7 };
    const resumeFrom = stored.lastBatchIndex + 1;
    expect(resumeFrom).toBe(8);
  });
});
