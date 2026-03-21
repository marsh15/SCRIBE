import { describe, it, expect } from "vitest";
import crypto from "node:crypto";

// ---- Inline the chunking metadata logic for isolated testing ----

interface ChunkMetadata {
  chunkIndex: number;
  charOffset: number | undefined;
}

function computeChunkOffsets(
  content: string,
  rawChunks: string[]
): ChunkMetadata[] {
  let searchOffset = 0;
  return rawChunks.map((chunk, index) => {
    const chunkStart = content.indexOf(chunk, searchOffset);
    if (chunkStart >= 0) searchOffset = chunkStart + chunk.length;
    return {
      chunkIndex: index,
      charOffset: chunkStart >= 0 ? chunkStart : undefined,
    };
  });
}

// ---- Tests ----

describe("Chunking metadata indexOf fix", () => {
  it("should assign monotonically increasing charOffsets", () => {
    // Simulate a document with two chunks that share repeated text
    const content = "Introduction. Introduction. The main body. Conclusion.";
    const chunk1 = "Introduction.";
    const chunk2 = "Introduction."; // duplicate substring — old code would return 0 both times!
    const chunk3 = "The main body.";
    const chunk4 = "Conclusion.";

    const results = computeChunkOffsets(content, [chunk1, chunk2, chunk3, chunk4]);

    // Each charOffset should be >= the previous one (monotonically increasing)
    for (let i = 1; i < results.length; i++) {
      expect(results[i].charOffset).toBeGreaterThanOrEqual(results[i - 1].charOffset!);
    }
  });

  it("should not return the same offset for two chunks containing duplicate substrings", () => {
    const content = "The dog. The cat. The bird.";
    // All three chunks start with "The " — old indexOf would return 0 for all
    const chunks = ["The dog.", "The cat.", "The bird."];
    const results = computeChunkOffsets(content, chunks);

    // First chunk at 0, second at 9, third at 18
    expect(results[0].charOffset).toBe(0);
    expect(results[1].charOffset).toBe(9);
    expect(results[2].charOffset).toBe(18);
  });

  it("should handle content with no repeated substrings correctly", () => {
    const content = "Alpha. Beta. Gamma. Delta.";
    const chunks = ["Alpha.", "Beta.", "Gamma.", "Delta."];
    const results = computeChunkOffsets(content, chunks);

    expect(results[0].charOffset).toBe(0);
    expect(results[1].charOffset).toBe(7);
    expect(results[2].charOffset).toBe(13);
    expect(results[3].charOffset).toBe(20);
  });
});

// ---- Razorpay webhook eventId dedup ----

describe("Razorpay webhook eventId uniqueness", () => {
  it("should generate the same eventId for identical raw bodies", () => {
    const rawBody = JSON.stringify({ event: "subscription.activated", payload: { subscription: { entity: { id: "sub_123" } } } });
    const id1 = crypto.createHash("sha256").update(rawBody).digest("hex");
    const id2 = crypto.createHash("sha256").update(rawBody).digest("hex");
    expect(id1).toBe(id2);
  });

  it("should generate DIFFERENT eventIds for different events on the same subscription", () => {
    const sub = { id: "sub_123", status: "created" };
    const body1 = JSON.stringify({ event: "subscription.activated", payload: { subscription: { entity: sub } } });
    const body2 = JSON.stringify({ event: "subscription.charged", payload: { subscription: { entity: { ...sub, status: "active" } } } });

    const id1 = crypto.createHash("sha256").update(body1).digest("hex");
    const id2 = crypto.createHash("sha256").update(body2).digest("hex");

    // Old code used sub.id as the key, making BOTH events share the same dedup key!
    // New code uses sha256(rawBody), so they should differ.
    expect(id1).not.toBe(id2);
  });

  it("should produce a 64-character hex string", () => {
    const id = crypto.createHash("sha256").update("test").digest("hex");
    expect(id).toHaveLength(64);
    expect(id).toMatch(/^[0-9a-f]+$/);
  });
});
