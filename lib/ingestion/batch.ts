export const MAX_CHUNKS_PER_BATCH = 100;
export const MAX_CHUNK_CHARS = 5000;
export const MAX_PAYLOAD_BYTES = 2 * 1024 * 1024; // 2MB

export interface BatchChunkMetadata {
  chunkIndex: number;
  totalChunks: number;
  [key: string]: unknown;
}

export interface BatchChunk {
  content: string;
  metadata: BatchChunkMetadata;
}

export interface BatchRequestBody {
  fileId: number;
  batchIndex: number;
  totalBatches: number;
  chunks: BatchChunk[];
}

export interface ValidatedBatchRequest {
  fileId: number;
  batchIndex: number;
  totalBatches: number;
  totalChunks: number;
  chunks: BatchChunk[];
}

export function estimateTokens(text: string) {
  return Math.ceil(text.length / 4);
}

export function expectedChunkIndex(batchIndex: number, offsetInBatch: number) {
  return batchIndex * MAX_CHUNKS_PER_BATCH + offsetInBatch;
}

export function validateBatchRequest(
  body: BatchRequestBody
): { ok: true; value: ValidatedBatchRequest } | { ok: false; error: string } {
  const { fileId, batchIndex, totalBatches, chunks } = body;

  if (!Number.isInteger(fileId) || fileId <= 0) {
    return { ok: false, error: "Missing or invalid fileId." };
  }

  if (!Number.isInteger(batchIndex) || batchIndex < 0) {
    return { ok: false, error: "Missing or invalid batchIndex." };
  }

  if (!Number.isInteger(totalBatches) || totalBatches <= 0) {
    return { ok: false, error: "Missing or invalid totalBatches." };
  }

  if (!Array.isArray(chunks) || chunks.length === 0) {
    return { ok: false, error: "Chunks array is empty or not an array." };
  }

  if (chunks.length > MAX_CHUNKS_PER_BATCH) {
    return {
      ok: false,
      error: `Too many chunks: ${chunks.length}. Maximum is ${MAX_CHUNKS_PER_BATCH}.`,
    };
  }

  if (batchIndex >= totalBatches) {
    return {
      ok: false,
      error: `batchIndex ${batchIndex} must be less than totalBatches ${totalBatches}.`,
    };
  }

  const firstChunk = chunks[0];
  const totalChunks = firstChunk?.metadata?.totalChunks;
  if (!Number.isInteger(totalChunks) || totalChunks <= 0) {
    return { ok: false, error: "Chunk metadata is missing a valid totalChunks value." };
  }

  const expectedTotalBatches = Math.ceil(totalChunks / MAX_CHUNKS_PER_BATCH);
  if (totalBatches !== expectedTotalBatches) {
    return {
      ok: false,
      error: `totalBatches ${totalBatches} does not match expected ${expectedTotalBatches}.`,
    };
  }

  const expectedBatchSize =
    batchIndex === totalBatches - 1
      ? totalChunks - batchIndex * MAX_CHUNKS_PER_BATCH
      : MAX_CHUNKS_PER_BATCH;

  if (chunks.length !== expectedBatchSize) {
    return {
      ok: false,
      error: `Batch ${batchIndex} expected ${expectedBatchSize} chunks but received ${chunks.length}.`,
    };
  }

  const seenChunkIndexes = new Set<number>();

  for (let i = 0; i < chunks.length; i++) {
    const chunk = chunks[i];
    if (!chunk.content || typeof chunk.content !== "string") {
      return { ok: false, error: `Chunk ${i} has invalid or missing content.` };
    }

    if (chunk.content.length > MAX_CHUNK_CHARS) {
      return {
        ok: false,
        error: `Chunk ${i} exceeds max length: ${chunk.content.length} chars (max ${MAX_CHUNK_CHARS}).`,
      };
    }

    const chunkIndex = chunk.metadata?.chunkIndex;
    if (!Number.isInteger(chunkIndex) || chunkIndex < 0) {
      return { ok: false, error: `Chunk ${i} is missing a valid metadata.chunkIndex.` };
    }

    if (chunk.metadata.totalChunks !== totalChunks) {
      return {
        ok: false,
        error: `Chunk ${i} has inconsistent metadata.totalChunks (${chunk.metadata.totalChunks}).`,
      };
    }

    if (chunkIndex >= totalChunks) {
      return {
        ok: false,
        error: `Chunk ${i} has out-of-range chunkIndex ${chunkIndex} for totalChunks ${totalChunks}.`,
      };
    }

    const expectedIndex = expectedChunkIndex(batchIndex, i);
    if (chunkIndex !== expectedIndex) {
      return {
        ok: false,
        error: `Chunk ${i} expected chunkIndex ${expectedIndex} but received ${chunkIndex}.`,
      };
    }

    if (seenChunkIndexes.has(chunkIndex)) {
      return {
        ok: false,
        error: `Duplicate chunkIndex ${chunkIndex} detected in batch ${batchIndex}.`,
      };
    }

    seenChunkIndexes.add(chunkIndex);
  }

  return {
    ok: true,
    value: {
      fileId,
      batchIndex,
      totalBatches,
      totalChunks,
      chunks,
    },
  };
}

export function summarizeInsertedChunks(
  chunks: BatchChunk[],
  insertedChunkIndexes: number[]
) {
  const insertedSet = new Set(insertedChunkIndexes);
  const insertedChunks = chunks.filter((chunk) =>
    insertedSet.has(chunk.metadata.chunkIndex)
  );
  const insertedText = insertedChunks.map((chunk) => chunk.content).join("");

  return {
    insertedChunks,
    insertedChunkCount: insertedChunks.length,
    insertedText,
    insertedChunkIndexes: insertedChunks.map((chunk) => chunk.metadata.chunkIndex),
  };
}

export function analyzeFinalizeState(
  storedChunkIndexes: number[],
  expectedTotalChunks: number
) {
  const uniqueChunkIndexes = Array.from(new Set(storedChunkIndexes)).sort((a, b) => a - b);
  const chunkIndexSet = new Set(uniqueChunkIndexes);
  const missingChunkIndexes: number[] = [];

  for (let index = 0; index < expectedTotalChunks; index++) {
    if (!chunkIndexSet.has(index)) {
      missingChunkIndexes.push(index);
    }
  }

  return {
    storedChunkCount: uniqueChunkIndexes.length,
    missingChunkIndexes,
    isComplete:
      uniqueChunkIndexes.length === expectedTotalChunks &&
      missingChunkIndexes.length === 0,
  };
}
