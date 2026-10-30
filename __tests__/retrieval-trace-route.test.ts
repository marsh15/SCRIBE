import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ resolveWorkspace: vi.fn(), callIntelligence: vi.fn() }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: mocks.resolveWorkspace }));
vi.mock("@/lib/auth", () => ({
  isNotAuthenticatedError: (error: unknown) => error instanceof Error && error.message === "not_authenticated",
}));
vi.mock("@/lib/intelligence-client", () => ({ callIntelligence: mocks.callIntelligence }));
import { GET } from "@/app/api/retrieval-traces/[id]/route";

const ID = "22222222-2222-4222-8222-222222222222";
function get(id = ID) {
  return GET(new Request(`https://scribe.example/api/retrieval-traces/${id}?workspaceId=forged`), {
    params: Promise.resolve({ id }),
  });
}
const reply = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
function details() {
  return {
    trace: {
      id: ID, query: "annual leave", profile: "hybrid-rrf", config_hash: "hash", status: "ok", degraded: [],
      stage_ms: { dense: 12 }, counts: { returned: 1, profile: "hybrid-rrf" }, cost: null,
      created_at: "2026-10-30T00:00:00+00:00",
    },
    answerability: { state: "ANSWERABLE", top_rerank_score: null },
    candidates: [], candidates_truncated: false,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "workspace_from_session" });
});

describe("GET exact retrieval trace", () => {
  it("authenticates before reading a resource and never caches failures", async () => {
    mocks.resolveWorkspace.mockRejectedValue(new Error("not_authenticated"));
    const response = await get();
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("rejects malformed identifiers without contacting the service", async () => {
    const response = await get("not-a-uuid");
    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(mocks.callIntelligence).not.toHaveBeenCalled();
  });

  it("uses only authenticated identity with the dedicated scope and exact trace ID", async () => {
    mocks.callIntelligence.mockResolvedValue(reply(200, details()));
    const response = await get();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const [path, token, init] = mocks.callIntelligence.mock.calls[0];
    expect(path).toBe("/retrieval/trace");
    expect(token).toEqual({ sub: "user_1", wid: "workspace_from_session", scope: "retrieval_trace_read" });
    expect(JSON.parse(init.body)).toEqual({ trace_id: ID });
    expect(init.signal).toBeInstanceOf(AbortSignal);
    expect((await response.json()).trace.id).toBe(ID);
  });

  it("strips document text and arbitrary metadata and redacts unavailable candidate identities", async () => {
    const body = {
      ...details(),
      trace: { ...details().trace, counts: { returned: 1, private_payload: "PRIVATE" }, quote: "PRIVATE" },
      candidates: [{
        id: ID, chunk_id: ID, source_id: 7, source_name: "PRIVATE", source_version_id: ID, version_number: 2,
        dense_rank: 1, dense_score: 0.8, lexical_rank: null, lexical_score: null,
        fusion_rank: 1, rerank_rank: null, rerank_score: null, selected: true, source_unavailable: true,
        content: "PRIVATE", quote: "PRIVATE",
      }],
    };
    mocks.callIntelligence.mockResolvedValue(reply(200, body));
    const response = await get();
    expect(response.status).toBe(200);
    const parsed = await response.json();
    expect(JSON.stringify(parsed)).not.toContain("PRIVATE");
    expect(parsed.trace.counts).toEqual({ returned: 1 });
    expect(parsed.candidates[0]).toMatchObject({
      chunk_id: null, source_id: null, source_name: null, source_version_id: null, version_number: null,
    });
  });

  it("returns the same generic response for a foreign and a missing trace", async () => {
    mocks.callIntelligence.mockResolvedValue(reply(404, { query: "PRIVATE", error_code: "NOT_FOUND" }));
    const first = await get();
    const second = await get("33333333-3333-4333-8333-333333333333");
    expect(first.status).toBe(404);
    expect(await first.json()).toEqual(await second.json());
  });

  it("fails closed on malformed, mismatched or unavailable upstream data without logging it", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const mismatched = details();
    mismatched.trace.id = "33333333-3333-4333-8333-333333333333";
    for (const body of [{ query: "PRIVATE" }, mismatched]) {
      mocks.callIntelligence.mockResolvedValue(reply(200, body));
      const response = await get();
      expect(response.status).toBe(502);
      expect(JSON.stringify(await response.json())).not.toContain("PRIVATE");
    }
    mocks.callIntelligence.mockResolvedValue(reply(503, { message: "PRIVATE" }));
    expect((await get()).status).toBe(502);
    mocks.callIntelligence.mockRejectedValue(new Error("PRIVATE"));
    expect((await get()).status).toBe(502);
    expect(log).not.toHaveBeenCalled();
    log.mockRestore();
  });
});
