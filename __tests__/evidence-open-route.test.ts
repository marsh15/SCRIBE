import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  resolveWorkspace: vi.fn(),
  callIntelligence: vi.fn(),
}));

vi.mock("@/lib/workspace", () => ({ resolveWorkspace: mocks.resolveWorkspace }));
vi.mock("@/lib/auth", () => ({
  isNotAuthenticatedError: (e: unknown) => e instanceof Error && e.message === "not_authenticated",
}));
vi.mock("@/lib/intelligence-client", () => ({ callIntelligence: mocks.callIntelligence }));

import { GET } from "@/app/api/evidence/[id]/route";

const ID = "22222222-2222-4222-8222-222222222222";
const ctx = (id: string) => ({ params: Promise.resolve({ id }) });
const get = (id = ID) => GET(new Request(`https://scribe.example/api/evidence/${id}`), ctx(id));
const py = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "w-1" });
});

describe("GET /api/evidence/[id]", () => {
  it("is 401 when signed out and never calls the service", async () => {
    mocks.resolveWorkspace.mockRejectedValue(new Error("not_authenticated"));
    expect((await get()).status).toBe(401);
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("is 404 for a malformed id without calling the service", async () => {
    expect((await get("12")).status).toBe(404);
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("mints an evidence_read token for the session workspace only and returns the location", async () => {
    mocks.callIntelligence.mockResolvedValue(
      py(200, {
        source_deleted: false, item_id: ID, label: "E1", source_id: 7, source_name: "Handbook.pdf",
        source_version_id: "v-uuid", version_number: 2, mime_type: "application/pdf", quote: "20 days of leave",
        heading_path: ["Leave"], page: 4, ocr: false, ocr_notice: null,
        spans: [{ block_index: 3, page: 4, bbox: [1, 2, 30, 40], ocr: false, row_number: null, char_start: 0, char_end: 16 }],
      }),
    );
    const res = await get();
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    const [path, token, init] = mocks.callIntelligence.mock.calls[0];
    expect(path).toBe("/evidence/open");
    expect(token).toEqual({ sub: "user_1", wid: "w-1", scope: "evidence_read" });
    expect(JSON.parse(init.body)).toEqual({ item_id: ID });
    expect(await res.json()).toMatchObject({
      sourceDeleted: false, sourceId: 7, versionNumber: 2, sourceVersionId: "v-uuid", page: 4,
      quote: "20 days of leave", headingPath: ["Leave"],
      spans: [{ page: 4, bbox: [1, 2, 30, 40], rowNumber: null }],
    });
  });

  it("is 404 when another workspace's id is requested (the service says 404)", async () => {
    mocks.callIntelligence.mockResolvedValue(py(404, { error_code: "NOT_FOUND" }));
    const res = await get();
    expect(res.status).toBe(404);
    expect(JSON.stringify(await res.json())).not.toContain("quote");
  });

  it("returns sourceDeleted with the quote withheld", async () => {
    mocks.callIntelligence.mockResolvedValue(
      py(200, { source_deleted: true, item_id: ID, label: "E1", notice: "Source deleted", quote: "LEAK", source_name: "LEAK" }),
    );
    const j = await (await get()).json();
    expect(j).toEqual({ sourceDeleted: true, itemId: ID, label: "E1", notice: "Source deleted" });
  });

  it("drops a bbox the service sent for OCR text", async () => {
    mocks.callIntelligence.mockResolvedValue(
      py(200, {
        source_deleted: false, item_id: ID, label: "E1", source_id: 1, source_name: "scan.pdf", source_version_id: null,
        version_number: 1, mime_type: "application/pdf", quote: "q", heading_path: [], page: 2, ocr: true,
        ocr_notice: "OCR text — verify against original",
        spans: [{ block_index: 0, page: 2, bbox: [1, 2, 3, 4], ocr: true, row_number: null, char_start: 0, char_end: 1 }],
      }),
    );
    const j = await (await get()).json();
    expect(j.spans[0].bbox).toBeNull();
    expect(j.ocrNotice).toBe("OCR text — verify against original");
  });

  it("is 502 when the service fails or is unreachable", async () => {
    mocks.callIntelligence.mockResolvedValue(py(503, {}));
    expect((await get()).status).toBe(502);
    mocks.callIntelligence.mockRejectedValue(new Error("down"));
    expect((await get()).status).toBe(502);
  });
});
