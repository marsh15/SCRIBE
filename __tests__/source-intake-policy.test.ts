import { describe, expect, it, vi } from "vitest";
import {
  completeReservedSourceUpload,
  isRetryableSourceError,
  parseSourceUploadMetadata,
  retryDelayMinutes,
  shouldRetrySource,
  sourceMimeTypeForName,
  sourceUploadPath,
  validateCompletedSourceBlob,
  validateSourceUploadAllowance,
} from "@/lib/ingestion/source-intake-policy";

describe("Source intake production policy", () => {
  it("accepts supported metadata and rejects invalid uploads", () => {
    expect(
      parseSourceUploadMetadata({
        name: "policy.pdf",
        size: 2048,
        type: "application/pdf",
      })
    ).toEqual({ name: "policy.pdf", size: 2048, type: "application/pdf" });

    expect(() =>
      parseSourceUploadMetadata({ name: "malware.exe", size: 10, type: "application/x-msdownload" })
    ).toThrow(/Unsupported file type/);
    expect(() =>
      parseSourceUploadMetadata({ name: "empty.pdf", size: 0, type: "application/pdf" })
    ).toThrow(/Invalid file size/);
    expect(() =>
      parseSourceUploadMetadata({ name: "renamed.exe", size: 10, type: "application/pdf" })
    ).toThrow(/Unsupported file type/);
    expect(sourceMimeTypeForName("report.CSV")).toBe("text/csv");
  });

  it("rejects exhausted usage and files above the plan limit", () => {
    const metadata = { name: "policy.pdf", size: 2048, type: "application/pdf" };
    expect(() =>
      validateSourceUploadAllowance(
        metadata,
        { allowOverage: false, projectedOverageInr: 1 },
        { maxBytes: 4096, maxMb: 1, planCode: "free" }
      )
    ).toThrow(/usage limit/);
    expect(() =>
      validateSourceUploadAllowance(
        metadata,
        { allowOverage: false, projectedOverageInr: 0 },
        { maxBytes: 1024, maxMb: 0.001, planCode: "free" }
      )
    ).toThrow(/File exceeds plan limit/);
  });

  it("enforces the reserved private Blob identity", () => {
    const source = {
      id: 42,
      userId: "user_123",
      name: "Policy handbook.pdf",
      size: 4096,
      type: "application/pdf",
    };
    const pathname = sourceUploadPath(source.userId, source.id, source.name);

    expect(() =>
      validateCompletedSourceBlob(source, {
        url: "https://blob.example/source",
        pathname,
        size: 4096,
        contentType: "application/pdf",
      })
    ).not.toThrow();
    expect(() =>
      validateCompletedSourceBlob(source, {
        url: "https://blob.example/source",
        pathname,
        size: 1,
        contentType: "application/pdf",
      })
    ).toThrow(/does not match/);
  });

  it("uses the five-attempt retry schedule", () => {
    expect([1, 2, 3, 4].map(retryDelayMinutes)).toEqual([2, 4, 8, 16]);
    expect(shouldRetrySource(4)).toBe(true);
    expect(shouldRetrySource(5)).toBe(false);
  });

  it("does not retry deterministic extraction failures", () => {
    expect(
      isRetryableSourceError(
        "No text extracted from scanned.pdf. Scanned PDFs and image-only documents are not supported yet."
      )
    ).toBe(false);
    expect(isRetryableSourceError("Google Embedding API error 429")).toBe(true);
  });

  it("queues completion once and treats callback replay as idempotent", async () => {
    const source = {
      id: 42,
      userId: "user_123",
      name: "Policy handbook.pdf",
      size: 4096,
      type: "application/pdf",
      status: "uploading",
      storageUrl: null as string | null,
    };
    let transitionCount = 0;
    const charges = new Set<number>();
    const adapters = {
      loadSource: async () => source,
      deleteBlob: async () => undefined,
      transitionToQueued: async () => {
        transitionCount += 1;
        source.status = "queued";
        source.storageUrl = "https://blob.example/source";
        return true;
      },
      recordStorageUsage: async () => {
        charges.add(source.id);
      },
    };
    const input = {
      sourceId: source.id,
      userId: source.userId,
      blob: {
        url: "https://blob.example/source",
        pathname: sourceUploadPath(source.userId, source.id, source.name),
        size: source.size,
        contentType: source.type,
      },
    };

    expect(await completeReservedSourceUpload(input, adapters)).toMatchObject({ queued: true });
    expect(await completeReservedSourceUpload(input, adapters)).toMatchObject({ replayed: true });
    expect(transitionCount).toBe(1);
    expect(charges.size).toBe(1);
  });

  it("recovers a failed accounting write on a same-blob replay", async () => {
    const source = { id: 5, userId: "user_1", name: "a.pdf", size: 10, type: "application/pdf", status: "uploading", storageUrl: null as string | null };
    const blob = { url: "https://blob.example/a", pathname: sourceUploadPath(source.userId, source.id, source.name), size: 10, contentType: source.type };
    const charges = new Set<number>();
    let fail = true;
    const adapters = {
      loadSource: async () => source,
      deleteBlob: vi.fn(),
      transitionToQueued: async () => { source.status = "queued"; source.storageUrl = blob.url; return true; },
      recordStorageUsage: async () => { if (fail) { fail = false; throw new Error("usage unavailable"); } charges.add(source.id); },
    };
    const input = { sourceId: source.id, userId: source.userId, blob };
    await expect(completeReservedSourceUpload(input, adapters)).rejects.toThrow("usage unavailable");
    await completeReservedSourceUpload(input, adapters);
    await completeReservedSourceUpload(input, adapters);
    expect(charges.size).toBe(1);
    await expect(completeReservedSourceUpload({ ...input, blob: { ...blob, url: "https://blob.example/other" } }, adapters)).rejects.toThrow();
    expect(charges.size).toBe(1);
  });

  it("rechecks the stored blob after a concurrent completion", async () => {
    const source = { id: 5, userId: "user_1", name: "a.pdf", size: 10, type: "application/pdf", status: "uploading", storageUrl: null as string | null };
    const blob = { url: "https://blob.example/a", pathname: sourceUploadPath(source.userId, source.id, source.name), size: 10, contentType: source.type };
    for (const storedUrl of [blob.url, "https://blob.example/other", null]) {
      const recordStorageUsage = vi.fn();
      const loadSource = vi.fn().mockResolvedValueOnce(source).mockResolvedValueOnce(storedUrl ? { ...source, status: "queued", storageUrl: storedUrl } : null);
      const result = await completeReservedSourceUpload({ sourceId: source.id, userId: source.userId, blob }, { loadSource, deleteBlob: vi.fn(), transitionToQueued: async () => false, recordStorageUsage });
      expect(result.shouldKick).toBe(storedUrl === blob.url);
      expect(recordStorageUsage).toHaveBeenCalledTimes(storedUrl === blob.url ? 1 : 0);
    }
  });

  it("deletes an uploaded orphan when its reservation no longer exists", async () => {
    let deletedUrl = "";
    const result = await completeReservedSourceUpload(
      {
        sourceId: 99,
        userId: "user_missing",
        blob: {
          url: "https://blob.example/orphan",
          pathname: "sources/user_missing/99/orphan.pdf",
          size: 100,
          contentType: "application/pdf",
        },
      },
      {
        loadSource: async () => null,
        deleteBlob: async (url) => {
          deletedUrl = url;
        },
        transitionToQueued: async () => false,
        recordStorageUsage: async () => undefined,
      }
    );

    expect(result.orphanDeleted).toBe(true);
    expect(deletedUrl).toBe("https://blob.example/orphan");
  });
});
