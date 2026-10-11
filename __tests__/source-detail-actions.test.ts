import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ sql: vi.fn(), resolveWorkspace: vi.fn() }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => mocks.sql(strings.join("?"), values),
  db: {},
}));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: mocks.resolveWorkspace }));
vi.mock("@/lib/db-compat", () => ({ ensureDocumentsChunkIndexSchema: vi.fn() }));
vi.mock("@/lib/auth", () => ({ isNotAuthenticatedError: (e: unknown) => e instanceof Error && e.message === "NOT_AUTH" }));

import {
  getSourceChunks,
  getSourceHistory,
  getSourceOverview,
  getSourceStructure,
  getSourceVersionsPage,
} from "@/app/files/[id]/actions";

const WS = "22222222-2222-4222-8222-222222222222";
const header = {
  id: 7, name: "a.pdf", type: "application/pdf", size: 10, status: "ready", processing_error: null,
  created_at: "2026-01-01T00:00:00Z", reupload_required_at: null, reupload_reason: null, active_version_id: "v-1", active_version_number: 1,
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: WS });
});

describe("detail actions", () => {
  const actions = [
    ["structure", (id: number) => getSourceStructure(id)],
    ["chunks", (id: number) => getSourceChunks(id)],
    ["history", (id: number) => getSourceHistory(id)],
    ["versions", (id: number) => getSourceVersionsPage(id)],
    ["overview", (id: number) => getSourceOverview(id)],
  ] as const;

  it.each(actions)("%s: 404 without touching the database for an invalid id", async (_n, run) => {
    for (const id of [0, -3, 2.5, NaN]) expect(await run(id)).toEqual({ ok: false, status: 404, error: "Source not found" });
    expect(mocks.sql).not.toHaveBeenCalled();
  });

  it.each(actions)("%s: 404 for another workspace's or a soft-deleted Source (no visible row)", async (_n, run) => {
    mocks.sql.mockResolvedValue([]);
    expect(await run(7)).toEqual({ ok: false, status: 404, error: "Source not found" });
    // the workspace comes from the server session only
    for (const [, values] of mocks.sql.mock.calls) expect(values).toEqual(expect.arrayContaining([WS, "user_1"]));
  });

  it("returns a bounded page for the owner", async () => {
    mocks.sql.mockResolvedValueOnce([header]).mockResolvedValueOnce([]);
    const result = await getSourceStructure(7, { limit: 5000 });
    expect(result).toMatchObject({ ok: true, data: { items: [], limit: 200, nextOffset: null } });
  });

  it("overview carries the OCR page count and the re-upload note fields", async () => {
    mocks.sql
      .mockResolvedValueOnce([{ ...header, reupload_required_at: "2026-02-01T00:00:00Z", reupload_reason: "original gone" }])
      .mockResolvedValueOnce([{ ocr_pages_done: 3, ocr_block_pages: 3, ocr_blocks: 4 }]);
    const result = await getSourceOverview(7);
    expect(result).toMatchObject({ ok: true, data: { ocr: { pages: 3 }, header: { reuploadReason: "original gone" } } });
  });

  it("maps auth failures to 401 and other failures to 500 without leaking details", async () => {
    mocks.resolveWorkspace.mockRejectedValueOnce(new Error("NOT_AUTH"));
    expect(await getSourceChunks(7)).toMatchObject({ ok: false, status: 401 });
    mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: WS });
    mocks.sql.mockRejectedValueOnce(new Error("relation chunks secret-text"));
    const failed = await getSourceHistory(7);
    expect(failed).toEqual({ ok: false, status: 500, error: "Could not load this tab." });
  });
});
