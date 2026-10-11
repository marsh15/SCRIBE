import { describe, expect, it, vi } from "vitest";
import {
  completeReservedVersionUpload,
  validateCompletedVersionBlob,
  type CompleteOutcome,
  type ReservedVersion,
  type VersionCompletionAdapters,
} from "@/lib/ingestion/source-version-policy";
import { deriveVersionState, toVersionView, type SourceVersionRow } from "@/lib/ingestion/source-version-state";
import {
  sourceVersionUploadPath,
  sourceVersionUploadPrefix,
  SourceIntakeError,
  parseSourceUploadCompletionPayload,
} from "@/lib/ingestion/source-intake-policy";

const version: ReservedVersion = {
  id: "11111111-1111-4111-8111-111111111111",
  sourceId: 7,
  versionNumber: 2,
  sizeBytes: 100,
  mimeType: "text/markdown",
  uploadState: "uploading",
};
const blob = {
  url: "https://blob.example/v2.md",
  pathname: sourceVersionUploadPath("user_1", 7, 2, "notes v2.md"),
  size: 100,
  contentType: "text/markdown; charset=utf-8",
};

function adapters(overrides: Partial<VersionCompletionAdapters> & { outcome?: CompleteOutcome } = {}) {
  const { outcome = "queued", ...rest } = overrides;
  const a = {
    loadVersion: vi.fn(async () => version as ReservedVersion | null),
    hashBlob: vi.fn(async () => "abc123"),
    complete: vi.fn(async () => ({ outcome, versionNumber: 2, duplicateOf: outcome === "duplicate" ? "dup-id" : null })),
    deleteBlob: vi.fn(async () => undefined),
    recordStorageUsage: vi.fn(async () => undefined),
    ...rest,
  };
  return a satisfies VersionCompletionAdapters;
}
const input = { versionId: version.id, sourceId: 7, userId: "user_1", blob };

describe("version upload paths", () => {
  it("gives every version its own folder so an original is never overwritten", () => {
    expect(sourceVersionUploadPath("user_1", 7, 2, "notes v2.md")).toBe("sources/user_1/7/v2/notes-v2.md");
    expect(sourceVersionUploadPath("user_1", 7, 3, "notes v2.md")).not.toBe(sourceVersionUploadPath("user_1", 7, 2, "notes v2.md"));
    expect(sourceVersionUploadPrefix("user_1", 7, 2)).toBe("sources/user_1/7/v2/");
  });
});

describe("completion payload", () => {
  it("accepts an optional versionId and rejects a malformed one", () => {
    expect(parseSourceUploadCompletionPayload(JSON.stringify({ sourceId: 7, userId: "u" })).versionId).toBeUndefined();
    expect(
      parseSourceUploadCompletionPayload(JSON.stringify({ sourceId: 7, userId: "u", versionId: version.id })).versionId
    ).toBe(version.id);
    expect(() =>
      parseSourceUploadCompletionPayload(JSON.stringify({ sourceId: 7, userId: "u", versionId: "nope" }))
    ).toThrow(SourceIntakeError);
  });
});

describe("completeReservedVersionUpload", () => {
  it("hashes the stored bytes, completes atomically, and records usage once when it queues a build", async () => {
    const a = adapters();
    const result = await completeReservedVersionUpload(input, a);
    expect(result).toMatchObject({ queued: true, duplicate: false, replayed: false, versionNumber: 2 });
    expect(a.complete).toHaveBeenCalledWith({ versionId: version.id, url: blob.url, sha256: "abc123", size: 100 });
    expect(a.recordStorageUsage).toHaveBeenCalledTimes(1);
    expect(a.deleteBlob).not.toHaveBeenCalled();
  });

  it("treats a callback for a version that already left 'uploading' as a no-op (no download, no job)", async () => {
    const a = adapters({ loadVersion: vi.fn(async () => ({ ...version, uploadState: "stored" })) });
    const result = await completeReservedVersionUpload(input, a);
    expect(result).toMatchObject({ queued: false, replayed: true });
    expect(a.hashBlob).not.toHaveBeenCalled();
    expect(a.complete).not.toHaveBeenCalled();
    expect(a.recordStorageUsage).not.toHaveBeenCalled();
  });

  it("reports a concurrent duplicate callback (database says replayed) without queueing or deleting the blob", async () => {
    const a = adapters({ outcome: "replayed" });
    const result = await completeReservedVersionUpload(input, a);
    expect(result).toMatchObject({ queued: false, replayed: true });
    expect(a.recordStorageUsage).not.toHaveBeenCalled();
    expect(a.deleteBlob).not.toHaveBeenCalled();
  });

  it("marks identical bytes as a duplicate, builds nothing and drops the redundant original", async () => {
    const a = adapters({ outcome: "duplicate" });
    const result = await completeReservedVersionUpload(input, a);
    expect(result).toMatchObject({ queued: false, duplicate: true });
    expect(a.deleteBlob).toHaveBeenCalledWith(blob.url);
    expect(a.recordStorageUsage).not.toHaveBeenCalled();
  });

  it("still succeeds when the redundant blob cannot be deleted", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const a = adapters({ outcome: "duplicate", deleteBlob: vi.fn(async () => Promise.reject(new Error("boom"))) });
    await expect(completeReservedVersionUpload(input, a)).resolves.toMatchObject({ duplicate: true });
    warn.mockRestore();
  });

  it("deletes the blob when the version or Source is gone", async () => {
    const missing = adapters({ loadVersion: vi.fn(async () => null) });
    await expect(completeReservedVersionUpload(input, missing)).resolves.toMatchObject({ orphanDeleted: true });
    expect(missing.deleteBlob).toHaveBeenCalledWith(blob.url);
    expect(missing.complete).not.toHaveBeenCalled();
    const deleted = adapters({ outcome: "source_deleted" });
    await expect(completeReservedVersionUpload(input, deleted)).resolves.toMatchObject({ orphanDeleted: true, queued: false });
    expect(deleted.deleteBlob).toHaveBeenCalledTimes(1);
  });

  it("rejects a blob that does not match its reservation before hashing anything", async () => {
    for (const bad of [
      { ...blob, pathname: "sources/user_1/7/v3/other.md" },
      { ...blob, pathname: "sources/other_user/7/v2/x.md" },
      { ...blob, size: 5 },
      { ...blob, contentType: "application/pdf" },
    ]) {
      const a = adapters();
      await expect(completeReservedVersionUpload({ ...input, blob: bad }, a)).rejects.toMatchObject({
        status: 409,
        code: "blob_mismatch",
      });
      expect(a.hashBlob).not.toHaveBeenCalled();
    }
    expect(() => validateCompletedVersionBlob("user_1", version, blob)).not.toThrow();
  });

  it("rejects a version that belongs to a different Source than the token says", async () => {
    const a = adapters({ loadVersion: vi.fn(async () => ({ ...version, sourceId: 99 })) });
    await expect(completeReservedVersionUpload(input, a)).rejects.toMatchObject({ code: "invalid_completion" });
  });
});

describe("deriveVersionState", () => {
  const base: SourceVersionRow = {
    id: "v",
    versionNumber: 1,
    sizeBytes: 10,
    createdAt: "2026-01-01T00:00:00Z",
    uploadState: "stored",
    duplicateOf: null,
    duplicateOfNumber: null,
    isActive: false,
    isDesired: false,
    generationStatus: null,
    jobStatus: null,
    jobStage: null,
    jobAttempts: 0,
    errorCode: null,
    lastError: null,
  };
  const state = (over: Partial<SourceVersionRow>) => deriveVersionState({ ...base, ...over });

  it("covers every lifecycle state", () => {
    expect(state({ uploadState: "uploading" })).toBe("uploading");
    expect(state({ uploadState: "failed" })).toBe("upload_failed");
    expect(state({ duplicateOf: "x", duplicateOfNumber: 1 })).toBe("duplicate");
    expect(state({ isActive: true, generationStatus: "ACTIVE" })).toBe("active");
    expect(state({ generationStatus: "SUPERSEDED" })).toBe("superseded");
    expect(state({ generationStatus: "CANCELLED", jobStatus: "superseded" })).toBe("cancelled");
    expect(state({ jobStatus: "superseded" })).toBe("cancelled");
    expect(state({ generationStatus: "FAILED", jobStatus: "failed" })).toBe("failed");
    expect(state({ jobStatus: "failed" })).toBe("failed");
    expect(state({ jobStatus: "queued" })).toBe("queued");
    expect(state({ jobStatus: "retrying", generationStatus: "BUILDING" })).toBe("retrying");
    expect(state({ jobStatus: "processing", generationStatus: "BUILDING" })).toBe("building");
    expect(state({ generationStatus: "READY" })).toBe("building");
  });

  it("keeps the previous version Active while a newer one fails, and labels duplicates", () => {
    const v1 = toVersionView({ ...base, isActive: true, generationStatus: "ACTIVE" });
    const v2 = toVersionView({ ...base, versionNumber: 2, jobStatus: "failed", errorCode: "RATE_LIMITED" });
    const v3 = toVersionView({ ...base, versionNumber: 3, duplicateOf: "v", duplicateOfNumber: 1 });
    expect([v1.label, v2.label, v3.label]).toEqual(["Active", "Failed", "Duplicate"]);
  });
});
