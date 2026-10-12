import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  sql: vi.fn(),
  deleteBlob: vi.fn(),
  dbDelete: vi.fn(),
  env: { INGEST_ENGINE: "python", INTERNAL_SERVICE_SECRET: "secret" } as Record<string, string | undefined>,
}));

vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => mocks.sql(strings.join("?"), values),
  db: { delete: mocks.dbDelete, query: { files: { findFirst: vi.fn() } } },
}));
vi.mock("@/lib/env", () => ({ env: mocks.env, requireEnv: () => "blob-token" }));
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
import { purgeSoftDeletedSources } from "@/lib/ingestion/source-delete";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.sql.mockReset();
  mocks.env.INGEST_ENGINE = "python";
  mocks.env.INTERNAL_SERVICE_SECRET = "secret";
});

describe("deleteSource, Python engine configured (T20 soft delete, INV-14)", () => {
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

describe("deleteSource, legacy engine or no internal secret (P0-3 hard delete)", () => {
  const refs = {
    storage_url: "https://abc.private.blob.vercel-storage.com/u/1/a.pdf",
    storage_key: "u/1/a.pdf",
    version_keys: ["u/1/a.pdf", "u/1/v2.pdf"],
  };

  for (const [label, engine, secret] of [
    ["INGEST_ENGINE=legacy", "legacy", "secret"],
    ["python engine without INTERNAL_SERVICE_SECRET", "python", undefined],
  ] as const) {
    it(`${label}: deletes every blob (original + versions, once each) then the rows, no soft delete`, async () => {
      mocks.env.INGEST_ENGINE = engine;
      mocks.env.INTERNAL_SERVICE_SECRET = secret;
      mocks.sql.mockResolvedValueOnce([refs]).mockResolvedValueOnce([{ id: 42 }]);
      expect(await deleteSource("user_1", 42)).toBe(true);
      expect(mocks.deleteBlob.mock.calls.map((c) => c[0])).toEqual([
        "https://abc.private.blob.vercel-storage.com/u/1/a.pdf",
        "u/1/v2.pdf",
      ]);
      const texts = mocks.sql.mock.calls.map((c) => c[0] as string);
      expect(texts[1]).toContain('DELETE FROM "files"');
      expect(texts[1]).toContain('"evidence_items"');
      expect(texts[1]).toContain('"rag_trace_chunks"');
      expect(texts.some((t) => t.includes("scribe_soft_delete_source"))).toBe(false);
      // blobs are deleted BEFORE the rows
      expect(mocks.deleteBlob.mock.invocationCallOrder[0]).toBeLessThan(mocks.sql.mock.invocationCallOrder[1]);
      // scoped by workspace and owner
      expect(mocks.sql.mock.calls[1][1]).toContain("user_1");
      expect(mocks.sql.mock.calls[1][1]).toContain("22222222-2222-4222-8222-222222222222");
    });
  }

  it("returns false and deletes nothing for an unknown or foreign Source", async () => {
    mocks.env.INGEST_ENGINE = "legacy";
    mocks.sql.mockResolvedValueOnce([]);
    expect(await deleteSource("user_1", 42)).toBe(false);
    expect(mocks.deleteBlob).not.toHaveBeenCalled();
    expect(mocks.sql).toHaveBeenCalledTimes(1);
  });

  it("a blob failure keeps the rows, records a backoff and falls back to a soft delete (hidden now, retried by the sweep)", async () => {
    mocks.env.INGEST_ENGINE = "legacy";
    mocks.deleteBlob.mockRejectedValueOnce(new Error("storage down"));
    mocks.sql
      .mockResolvedValueOnce([refs])
      .mockResolvedValueOnce([]) // backoff update
      .mockResolvedValueOnce([{ deleted: true }]);
    expect(await deleteSource("user_1", 42)).toBe(true);
    const texts = mocks.sql.mock.calls.map((c) => c[0] as string);
    expect(texts.some((t) => t.includes('DELETE FROM "files"'))).toBe(false);
    expect(texts[1]).toContain("purge_next_retry_at");
    expect(texts[2]).toContain("scribe_soft_delete_source");
  });
});

describe("purgeSoftDeletedSources (cleanup of rows left by the interim soft delete)", () => {
  it("hard-purges soft-deleted rows only, in a bounded batch, and is idempotent", async () => {
    mocks.sql
      .mockResolvedValueOnce([{ id: 7, workspace_id: "w1" }, { id: 8, workspace_id: "w2" }])
      .mockResolvedValueOnce([{ storage_url: null, storage_key: "k7", version_keys: null }])
      .mockResolvedValueOnce([{ id: 7 }])
      .mockResolvedValueOnce([]); // second row vanished concurrently: "missing", not an error
    expect(await purgeSoftDeletedSources(5)).toEqual({ listed: 2, purged: 1, blobFailed: 0 });
    const list = mocks.sql.mock.calls[0];
    expect(list[0]).toContain('"deleted_at" IS NOT NULL');
    expect(list[0]).toContain("LIMIT");
    expect(list[1]).toContain(5);
    expect(mocks.deleteBlob).toHaveBeenCalledWith("k7");
    // the per-row statements only touch rows that are still soft-deleted
    expect(mocks.sql.mock.calls[1][1]).toContain(true);
    expect(mocks.sql.mock.calls[2][1]).toContain(true);
  });

  it("with nothing left it does nothing", async () => {
    mocks.sql.mockResolvedValueOnce([]);
    expect(await purgeSoftDeletedSources()).toEqual({ listed: 0, purged: 0, blobFailed: 0 });
    expect(mocks.deleteBlob).not.toHaveBeenCalled();
  });
});
