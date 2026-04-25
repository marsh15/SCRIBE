import { describe, it, expect } from "vitest";
import {
  analyzeFinalizeState,
  estimateTokens,
  summarizeInsertedChunks,
  validateBatchRequest,
} from "@/lib/ingestion/batch";

function makeChunk(chunkIndex: number, totalChunks = 100, length = 100) {
  return {
    content: "a".repeat(length),
    metadata: {
      chunkIndex,
      totalChunks,
      section: 1,
      charLength: length,
    },
  };
}

describe("Batch ingest validation", () => {
  it("accepts a valid first batch", () => {
    const result = validateBatchRequest({
      fileId: 42,
      batchIndex: 0,
      totalBatches: 1,
      chunks: [makeChunk(0, 1)],
    });

    expect(result.ok).toBe(true);
  });

  it("rejects inconsistent totalBatches", () => {
    const result = validateBatchRequest({
      fileId: 42,
      batchIndex: 0,
      totalBatches: 2,
      chunks: [makeChunk(0, 1)],
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("totalBatches");
    }
  });

  it("rejects mismatched chunk indexes for a batch", () => {
    const result = validateBatchRequest({
      fileId: 42,
      batchIndex: 1,
      totalBatches: 2,
      chunks: [makeChunk(42, 101)],
    });

    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error).toContain("expected chunkIndex 100");
    }
  });
});

describe("Batch replay idempotency", () => {
  it("counts only newly inserted chunk indexes", () => {
    const chunks = [makeChunk(0, 2, 40), makeChunk(1, 2, 80)];

    const firstInsert = summarizeInsertedChunks(chunks, [0, 1]);
    expect(firstInsert.insertedChunkCount).toBe(2);

    const replayInsert = summarizeInsertedChunks(chunks, []);
    expect(replayInsert.insertedChunkCount).toBe(0);
  });

  it("does not produce additional usage for a replayed batch", () => {
    const chunks = [makeChunk(0, 2, 40), makeChunk(1, 2, 80)];
    const replayInsert = summarizeInsertedChunks(chunks, []);

    expect(estimateTokens(replayInsert.insertedText)).toBe(0);
  });
});

describe("Finalize integrity checks", () => {
  it("detects missing chunk indexes before finalize can succeed", () => {
    const result = analyzeFinalizeState([0, 1, 3], 4);

    expect(result.isComplete).toBe(false);
    expect(result.storedChunkCount).toBe(3);
    expect(result.missingChunkIndexes).toEqual([2]);
  });
});

describe("Resumability localStorage state", () => {
  it("serializes and deserializes progress correctly", () => {
    const progress = {
      fileId: 42,
      lastBatchIndex: 7,
      totalBatches: 11,
      chunks: [makeChunk(0, 1, 4)],
      textBytes: 50000,
    };

    const serialized = JSON.stringify(progress);
    const deserialized = JSON.parse(serialized);

    expect(deserialized.fileId).toBe(42);
    expect(deserialized.lastBatchIndex).toBe(7);
    expect(deserialized.totalBatches).toBe(11);
    expect(deserialized.chunks).toHaveLength(1);
    expect(deserialized.chunks[0].content).toBe("aaaa");
  });
});
