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

import { POST } from "@/app/api/compare/route";

const post = (body: unknown, raw?: string) =>
  POST(new Request("https://scribe.example/api/compare", { method: "POST", body: raw ?? JSON.stringify(body) }));
const py = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const OK = { compare_id: "c1", profiles: ["dense-only", "hybrid-rrf"], profile_results: [], alignment: { baseline: "dense-only", rows: [], summary: {} } };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "w-1" });
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

describe("POST /api/compare", () => {
  it("is 401 when signed out and never calls the service", async () => {
    mocks.resolveWorkspace.mockRejectedValue(new Error("not_authenticated"));
    const res = await post({ query: "q", profiles: ["a", "b"] });
    expect(res.status).toBe(401);
    expect((await res.json()).error.code).toBe("UNAUTHENTICATED");
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("is 400 for a body that is not a JSON object", async () => {
    expect((await post(null, "not json")).status).toBe(400);
    expect((await post([1, 2])).status).toBe(400);
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("mints a compare token for the session workspace and forwards only the whitelisted fields", async () => {
    mocks.callIntelligence.mockResolvedValue(py(200, OK));
    const res = await post({
      query: "leave policy", profiles: ["dense-only", "hybrid-rrf"], limit: 5, sourceIds: [3, 4],
      workspace_id: "w-EVIL", wid: "w-EVIL", userId: "evil", scope: "admin",
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(await res.json()).toEqual(OK);
    const [path, token, init] = mocks.callIntelligence.mock.calls[0];
    expect(path).toBe("/compare");
    expect(token).toEqual({ sub: "user_1", wid: "w-1", scope: "compare" });
    expect(JSON.parse(init.body)).toEqual({ query: "leave policy", profiles: ["dense-only", "hybrid-rrf"], limit: 5, source_ids: [3, 4] });
  });

  it("passes the service's 404 (unknown profile) through with the list of comparable profiles", async () => {
    mocks.callIntelligence.mockResolvedValue(py(404, { error_code: "UNKNOWN_PROFILE", unknown: ["nope"], profiles: ["dense-only"] }));
    const res = await post({ query: "q", profiles: ["dense-only", "nope"] });
    expect(res.status).toBe(404);
    expect(await res.json()).toMatchObject({ error: { code: "UNKNOWN_PROFILE", unknown: ["nope"], profiles: ["dense-only"] } });
  });

  it("passes the service's 422 through without echoing the offending input", async () => {
    mocks.callIntelligence.mockResolvedValue(
      py(422, { detail: [{ type: "too_short", loc: ["body", "profiles"], msg: "List should have at least 2 items", input: ["SECRET-QUERY-ECHO"] }] }),
    );
    const res = await post({ query: "q", profiles: ["dense-only"] });
    expect(res.status).toBe(422);
    const j = await res.json();
    expect(j.error.code).toBe("INVALID_REQUEST");
    expect(j.error.issues).toEqual([{ field: "profiles", message: "List should have at least 2 items" }]);
    expect(JSON.stringify(j)).not.toContain("SECRET-QUERY-ECHO");
  });

  it("maps retrieval outages and other failures without leaking service details", async () => {
    mocks.callIntelligence.mockResolvedValue(py(503, { error_code: "RETRIEVAL_FAILED", cause: "embedding secret detail" }));
    let res = await post({ query: "q", profiles: ["a", "b"] });
    expect(res.status).toBe(503);
    expect(JSON.stringify(await res.json())).not.toContain("secret detail");
    mocks.callIntelligence.mockResolvedValue(py(500, { error_code: "RERANK_CACHE_MISS" }));
    res = await post({ query: "q", profiles: ["a", "b"] });
    expect(res.status).toBe(502);
    mocks.callIntelligence.mockRejectedValue(new Error("connect ECONNREFUSED 10.0.0.1"));
    res = await post({ query: "q", profiles: ["a", "b"] });
    expect(res.status).toBe(502);
    expect(JSON.stringify(await res.json())).not.toContain("ECONNREFUSED");
    mocks.callIntelligence.mockResolvedValue(new Response("<html>not json", { status: 200 }));
    expect((await post({ query: "q", profiles: ["a", "b"] })).status).toBe(502);
  });
});
