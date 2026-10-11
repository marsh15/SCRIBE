import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ resolveWorkspace: vi.fn(), resolveVersionBlob: vi.fn(), download: vi.fn() }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: mocks.resolveWorkspace }));
vi.mock("@/lib/db-config", () => ({ db: {}, sql: vi.fn() }));
vi.mock("@/lib/files/source-detail", () => ({ resolveVersionBlob: mocks.resolveVersionBlob }));
vi.mock("@/lib/storage/blob", () => ({ downloadBlobToBuffer: mocks.download }));

import { GET } from "@/app/api/files/[id]/view/route";

const ctx = (id: string) => ({ params: Promise.resolve({ id }) });
const get = (qs: string, id = "7") => GET(new Request(`https://scribe.example/api/files/${id}/view${qs}`), ctx(id));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "w-1" });
});

describe("GET /api/files/[id]/view?version=", () => {
  it("serves a duplicate version from the blob it resolves to, scoped to the session workspace and user", async () => {
    mocks.resolveVersionBlob.mockResolvedValue({
      url: "https://blob.example/v2.md", mimeType: "text/markdown", fileName: "a b.md", versionNumber: 3, blobVersionNumber: 2,
    });
    mocks.download.mockResolvedValue(new Uint8Array([104, 105]));
    const res = await get("?version=3");
    expect(res.status).toBe(200);
    expect(mocks.resolveVersionBlob).toHaveBeenCalledWith("w-1", "user_1", 7, 3);
    expect(mocks.download).toHaveBeenCalledWith("https://blob.example/v2.md");
    expect(res.headers.get("Content-Type")).toBe("text/markdown");
    expect(await res.text()).toBe("hi");
  });

  it("is 404 when the version cannot be resolved (foreign, deleted or unknown)", async () => {
    mocks.resolveVersionBlob.mockResolvedValue(null);
    expect((await get("?version=3")).status).toBe(404);
    expect(mocks.download).not.toHaveBeenCalled();
  });

  it.each(["?version=0", "?version=abc", "?version=1.5", "?version=-2"])("is 404 for a malformed version (%s) without a lookup", async (qs) => {
    expect((await get(qs)).status).toBe(404);
    expect(mocks.resolveVersionBlob).not.toHaveBeenCalled();
  });
});
