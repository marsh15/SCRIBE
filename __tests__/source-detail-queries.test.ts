import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ sql: vi.fn() }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => mocks.sql(strings.join("?"), values),
  db: {},
}));

import {
  getOcrSummary,
  getSourceHeader,
  listChunks,
  listIngestionHistory,
  listStructure,
  resolveVersionBlob,
} from "@/lib/files/source-detail";
import { listSourceVersions } from "@/lib/ingestion/source-versions";

const WS = "22222222-2222-4222-8222-222222222222";
const header = {
  id: 7, name: "a.pdf", type: "application/pdf", size: 10, status: "ready", processing_error: null,
  created_at: new Date("2026-01-01T00:00:00Z"), reupload_required_at: null, reupload_reason: null,
  active_version_id: "v-1", active_version_number: 1,
};

beforeEach(() => vi.clearAllMocks());

describe("getSourceHeader", () => {
  it("scopes by workspace, user and deleted_at in the statement", async () => {
    mocks.sql.mockResolvedValueOnce([header]);
    const h = await getSourceHeader(WS, "user_1", 7);
    expect(h).toMatchObject({ id: 7, activeVersionNumber: 1, createdAt: "2026-01-01T00:00:00.000Z" });
    const [query, values] = mocks.sql.mock.calls[0];
    expect(values).toEqual([7, WS, "user_1"]);
    expect(query).toContain('"deleted_at" IS NULL');
    expect(query).toContain('"workspace_id" = ?::uuid');
  });

  it("returns null for a foreign or soft-deleted Source (no row) and for an invalid id without querying", async () => {
    mocks.sql.mockResolvedValueOnce([]);
    expect(await getSourceHeader(WS, "intruder", 7)).toBeNull();
    expect(await getSourceHeader(WS, "user_1", 0)).toBeNull();
    expect(await getSourceHeader(WS, "user_1", 1.5)).toBeNull();
    expect(mocks.sql).toHaveBeenCalledTimes(1);
  });
});

describe("pagination bounds reach the database", () => {
  const limitOf = (call: number) => {
    const values = mocks.sql.mock.calls[call][1] as unknown[];
    return { fetchLimit: values.at(-2), offset: values.at(-1) };
  };

  it("fetches 51 rows by default (50 + the probe row) and clamps to 201 at most", async () => {
    mocks.sql.mockResolvedValue([header]).mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listStructure(WS, "user_1", 7);
    expect(limitOf(1)).toEqual({ fetchLimit: 51, offset: 0 });

    mocks.sql.mockReset().mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listStructure(WS, "user_1", 7, { limit: 100000, offset: -4 });
    expect(limitOf(1)).toEqual({ fetchLimit: 201, offset: 0 });

    mocks.sql.mockReset().mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listChunks(WS, "user_1", 7, { limit: 0, offset: 100 });
    expect(limitOf(1)).toEqual({ fetchLimit: 2, offset: 100 });

    mocks.sql.mockReset().mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listIngestionHistory(WS, "user_1", 7, { limit: 200 });
    expect(limitOf(1)).toEqual({ fetchLimit: 201, offset: 0 });

    mocks.sql.mockReset().mockResolvedValueOnce([]);
    await listSourceVersions(WS, "user_1", 7, { limit: 9999 });
    expect(limitOf(0)).toEqual({ fetchLimit: 201, offset: 0 });
  });

  it("drops the probe row and reports the next offset", async () => {
    const rows = Array.from({ length: 3 }, (_, i) => ({
      id: `b${i}`, block_index: i, type: "paragraph", heading_path: ["H"], page: 1, ocr: i === 1, row_number: null,
      char_start: 0, char_end: 3, preview: "abc", text_length: 3,
    }));
    mocks.sql.mockResolvedValueOnce([header]).mockResolvedValueOnce(rows);
    const page = await listStructure(WS, "user_1", 7, { limit: 2, offset: 10 });
    expect(page?.items.map((b) => b.blockIndex)).toEqual([0, 1]);
    expect(page?.items[1].ocr).toBe(true);
    expect(page?.nextOffset).toBe(12);
  });
});

describe("workspace scoping and 404 semantics", () => {
  it.each([
    ["structure", () => listStructure(WS, "intruder", 7)],
    ["chunks", () => listChunks(WS, "intruder", 7)],
    ["history", () => listIngestionHistory(WS, "intruder", 7)],
  ])("%s returns null (404) for a Source the caller cannot see and runs no data query", async (_name, run) => {
    mocks.sql.mockResolvedValueOnce([]);
    expect(await run()).toBeNull();
    expect(mocks.sql).toHaveBeenCalledTimes(1);
  });

  it("every data query carries the workspace, user and deleted filters", async () => {
    for (const run of [
      () => listStructure(WS, "user_1", 7),
      () => listChunks(WS, "user_1", 7),
      () => listIngestionHistory(WS, "user_1", 7),
      () => getOcrSummary(WS, "user_1", 7),
      () => resolveVersionBlob(WS, "user_1", 7, 2),
    ]) {
      mocks.sql.mockReset().mockResolvedValue([header]);
      await run();
      const [query, values] = mocks.sql.mock.calls.at(-1)!;
      expect(values).toEqual(expect.arrayContaining([WS, "user_1", 7]));
      expect(query).toContain('"deleted_at" IS NULL');
      expect(query).toContain('"user_id" = ?');
      expect(query).toContain('"workspace_id" = ?::uuid');
    }
  });

  it("chunk queries read only the ACTIVE generation of the active version and filter by kind", async () => {
    mocks.sql.mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listChunks(WS, "user_1", 7, { kind: "parent" });
    const [query, values] = mocks.sql.mock.calls[1];
    expect(query).toContain("g.\"status\" = 'ACTIVE'");
    expect(query).toContain('g."source_version_id" = f."active_version_id"');
    expect(values).toContain("parent");
    mocks.sql.mockReset().mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    await listChunks(WS, "user_1", 7, { kind: "bogus" as never });
    expect(mocks.sql.mock.calls[1][1]).toContain("all");
  });

  it("maps chunk rows with spans and kinds", async () => {
    mocks.sql.mockResolvedValueOnce([header]).mockResolvedValueOnce([
      { id: "c1", parent_id: null, chunk_index: 0, is_parent: true, token_count: 300, heading_text: "Terms", excerpt: "x", content_length: 1, child_count: "2", spans: [] },
      { id: "c2", parent_id: "c1", chunk_index: 1, is_parent: false, token_count: 120, heading_text: "Terms", excerpt: "y", content_length: 1, child_count: null,
        spans: [{ page: 3, charStart: 0, charEnd: 40 }] },
    ]);
    const page = await listChunks(WS, "user_1", 7);
    expect(page?.items[0]).toMatchObject({ kind: "parent", childCount: 2, tokenCount: 300 });
    expect(page?.items[1]).toMatchObject({ kind: "child", parentId: "c1", spans: [{ page: 3, charStart: 0, charEnd: 40 }] });
  });
});

describe("OCR summary", () => {
  it("takes the larger of the progress table and OCR block pages", async () => {
    mocks.sql.mockResolvedValueOnce([{ ocr_pages_done: "2", ocr_block_pages: "5", ocr_blocks: "9" }]);
    expect(await getOcrSummary(WS, "user_1", 7)).toEqual({ pages: 5, blocks: 9 });
    mocks.sql.mockResolvedValueOnce([{ ocr_pages_done: "4", ocr_block_pages: "0", ocr_blocks: "0" }]);
    expect(await getOcrSummary(WS, "user_1", 7)).toEqual({ pages: 4, blocks: 0 });
    mocks.sql.mockResolvedValueOnce([]);
    expect(await getOcrSummary(WS, "user_1", 7)).toBeNull();
  });
});

describe("resolveVersionBlob", () => {
  const base = { name: "a.md", type: "text/markdown", storage_url: "https://blob.example/legacy.md", mime_type: "text/markdown", upload_state: "stored" };

  it("serves a duplicate version from the version it duplicates (its own blob is deleted)", async () => {
    // version 3 is a duplicate of version 2: the join row carries version 2's key
    mocks.sql.mockResolvedValueOnce([{ ...base, version_number: 3, blob_key: "https://blob.example/v2.md", blob_version_number: 2 }]);
    const blob = await resolveVersionBlob(WS, "user_1", 7, 3);
    expect(blob).toEqual({ url: "https://blob.example/v2.md", mimeType: "text/markdown", fileName: "a.md", versionNumber: 3, blobVersionNumber: 2 });
    expect(mocks.sql.mock.calls[0][0]).toContain('COALESCE(v."duplicate_of", v."id")');
  });

  it("falls back to the Source row URL only for a legacy first version", async () => {
    mocks.sql.mockResolvedValueOnce([{ ...base, version_number: 1, blob_key: "uploads/u/a.md", blob_version_number: 1 }]);
    expect((await resolveVersionBlob(WS, "user_1", 7, null))?.url).toBe("https://blob.example/legacy.md");
    mocks.sql.mockResolvedValueOnce([{ ...base, version_number: 2, blob_key: "uploads/u/a.md", blob_version_number: 2 }]);
    expect(await resolveVersionBlob(WS, "user_1", 7, 2)).toBeNull();
  });

  it("is null for unknown/foreign/deleted Sources, unstored uploads, bad ids and a duplicate whose original is gone", async () => {
    mocks.sql.mockResolvedValueOnce([]);
    expect(await resolveVersionBlob(WS, "intruder", 7, 1)).toBeNull();
    mocks.sql.mockResolvedValueOnce([{ ...base, version_number: 2, upload_state: "uploading", blob_key: null, blob_version_number: 2 }]);
    expect(await resolveVersionBlob(WS, "user_1", 7, 2)).toBeNull();
    mocks.sql.mockResolvedValueOnce([{ ...base, version_number: 3, blob_key: null, blob_version_number: null }]);
    expect(await resolveVersionBlob(WS, "user_1", 7, 3)).toBeNull();
    expect(await resolveVersionBlob(WS, "user_1", 7, 0)).toBeNull();
    expect(await resolveVersionBlob(WS, "user_1", -1, null)).toBeNull();
  });
});
