import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ sql: vi.fn(), deleteBlob: vi.fn(), dbDelete: vi.fn() }));

vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => mocks.sql(strings.join("?"), values),
  db: { delete: mocks.dbDelete, query: { files: { findFirst: vi.fn() } } },
}));
vi.mock("@/lib/env", () => ({ env: { INGEST_ENGINE: "python" }, requireEnv: () => "blob-token" }));
vi.mock("@/lib/storage/blob", () => ({ deleteBlob: mocks.deleteBlob, downloadBlobToBuffer: vi.fn() }));
vi.mock("@/lib/workspace", () => ({
  ensurePersonalWorkspace: async () => "22222222-2222-4222-8222-222222222222",
}));
vi.mock("@/lib/billing/usage", () => ({ getUsageSummary: vi.fn(), recordUsageEvent: vi.fn() }));
vi.mock("@/lib/uploads/limits", () => ({ getUserMaxUploadBytes: vi.fn() }));
vi.mock("@/lib/ingestion/python-engine", () => ({ ingestSourceViaPython: vi.fn(), PermanentIngestError: class extends Error {} }));
vi.mock("@/lib/ingestion/extract", () => ({ extractTextFromBuffer: vi.fn() }));
vi.mock("@/lib/chunking", () => ({ chunkContent: vi.fn() }));
vi.mock("@/lib/embeddings", () => ({ generateEmbeddings: vi.fn() }));
vi.mock("@/lib/db-compat", () => ({ ensureDocumentsChunkIndexSchema: vi.fn() }));
vi.mock("@vercel/blob/client", () => ({ generateClientTokenFromReadWriteToken: vi.fn() }));

import { deleteSource } from "@/lib/ingestion/source-intake";

beforeEach(() => vi.clearAllMocks());

describe("deleteSource (T20 soft delete)", () => {
  it("soft deletes through ONE database function call and never hard-deletes or touches blobs", async () => {
    mocks.sql.mockResolvedValueOnce([{ deleted: true }]);
    expect(await deleteSource("user_1", 42)).toBe(true);
    expect(mocks.sql).toHaveBeenCalledTimes(1);
    const [text, values] = mocks.sql.mock.calls[0];
    expect(text).toContain("scribe_soft_delete_source");
    expect(values).toEqual(["22222222-2222-4222-8222-222222222222", "user_1", 42]);
    expect(mocks.dbDelete).not.toHaveBeenCalled();
    expect(mocks.deleteBlob).not.toHaveBeenCalled();
  });

  it("returns false for an unknown, foreign or already-deleted Source", async () => {
    mocks.sql.mockResolvedValueOnce([{ deleted: false }]);
    expect(await deleteSource("user_1", 42)).toBe(false);
  });

  it("rejects an invalid id without querying", async () => {
    expect(await deleteSource("user_1", -1)).toBe(false);
    expect(await deleteSource("user_1", 1.5)).toBe(false);
    expect(mocks.sql).not.toHaveBeenCalled();
  });
});
