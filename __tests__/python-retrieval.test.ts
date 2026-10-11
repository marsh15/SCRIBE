import { beforeEach, describe, expect, it, vi } from "vitest";

const envMock = vi.hoisted(() => ({
  RETRIEVAL_ENGINE: "legacy" as "legacy" | "python",
  INTERNAL_SERVICE_SECRET: "test-secret-test-secret-test-secret-32b",
  INTELLIGENCE_BASE_URL: "http://py.test",
  VERCEL_AUTOMATION_BYPASS_SECRET: undefined as string | undefined,
  VERCEL_URL: undefined as string | undefined,
}));

vi.mock("@/lib/env", () => ({
  env: envMock,
  requireEnv: (k: keyof typeof envMock) => envMock[k],
}));

import { decodeJwt } from "jose";
import {
  SEARCH_UNAVAILABLE_MESSAGE,
  getRetrievalEngine,
  NO_SOURCES_MESSAGE,
  retrieveViaPython,
  shouldUseSupermemory,
} from "@/lib/python-retrieval";

const WID = "11111111-1111-4111-8111-111111111111";

const okBody = {
  profile: "hybrid-rerank",
  trace_id: "t1",
  degraded: [] as string[],
  candidates: [
    {
      rank: 1, chunk_id: "c1", doc_name: "Handbook.pdf", heading_text: "Leave policy",
      content: "Employees get 20 days.", source_id: 7,
      dense_rank: 2, lexical_rank: 1, fusion_rank: 1, rerank_rank: 1, rerank_score: 0.9,
    },
  ],
  config_hash: "cfghash0123456789abcdef",
  stage_ms: {
    embed: 5, dense: 12, lexical_plus_embed_wait: 30, hydrate: 2, total: 20,
    fusion: 1, rerank: 9, dedup: 0, expand: 1, pack: 2, request_total: 40,
  },
  counts: { dense: 40, lexical: 40, fused: 50, reranked: 10, packed: 1, returned: 1 },
  context: {
    entries: [
      {
        chunk_id: "c1", text: "Employees get 20 days.", heading_text: "Leave policy", doc_name: "Handbook.pdf",
        source_id: 7, label: "E1", evidence_item_id: "item-1",
      },
    ],
  },
  answerability: {
    state: "ANSWERABLE", reason: "ok", top_rerank_score: 0.9, tau: 0, tau_calibrated: false, gate_degraded: false,
  },
  evidence: {
    bundle_id: "bundle-1",
    next_label: 2,
    items: [
      {
        id: "item-1", label: "E1", chunk_id: "c1", source_id: 7, source_name: "Handbook.pdf", source_version_id: "v1",
        page_start: 3, page_end: 3, heading_path: ["Leave policy"],
      },
    ],
  },
};

function mockFetch(impl: () => Promise<Response>) {
  const f = vi.fn(impl);
  vi.stubGlobal("fetch", f);
  return f;
}

describe("engine switch", () => {
  it("defaults to legacy and only python on exact value", () => {
    expect(getRetrievalEngine(undefined)).toBe("legacy");
    expect(getRetrievalEngine("legacy")).toBe("legacy");
    expect(getRetrievalEngine("bogus")).toBe("legacy");
    expect(getRetrievalEngine("python")).toBe("python");
  });

  it("disables Supermemory in python mode (D16)", () => {
    expect(shouldUseSupermemory("python", true)).toBe(false);
    expect(shouldUseSupermemory("legacy", true)).toBe(true);
    expect(shouldUseSupermemory("legacy", false)).toBe(false);
  });
});

describe("retrieveViaPython", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("posts to /api/py/retrieve with a scoped 60s token and server-side workspace", async () => {
    const f = mockFetch(async () => new Response(JSON.stringify(okBody), { status: 200 }));
    const result = await retrieveViaPython({ query: "leave?", userId: "user_1", workspaceId: WID });

    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://py.test/api/py/retrieve");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string);
    expect(body).toEqual({ query: "leave?", profile: "hybrid-rrf-rerank", limit: 10, next_label: 1 });
    expect(body.workspace_id).toBeUndefined();
    const auth = new Headers(init.headers).get("authorization")!;
    const claims = decodeJwt(auth.replace("Bearer ", ""));
    expect(claims).toMatchObject({ sub: "user_1", wid: WID, scope: "retrieve" });
    expect(claims.exp! - claims.iat!).toBe(60);

    expect(result.status).toBe("retrieved");
    expect(result.engine).toBe("python");
    expect(result.context).toContain("[E1] Source: Handbook.pdf | page 3 | Section: Leave policy");
    expect(result.retrieval?.profile).toBe("hybrid-rerank");
    expect(result.chunks[0].stageRanks).toEqual({ dense: 2, lexical: 1, fused: 1, reranked: 1 });
    expect(result.chunks[0].selected).toBe(true);
    expect(result.retrieval?.configHash).toBe("cfghash0123456789abcdef");
    expect(result.retrieval?.stageMs).toMatchObject({ dense: 12, lexical_plus_embed_wait: 30, fusion: 1, rerank: 9, pack: 2 });
    expect(result.timings.retrievalMs).toBe(12);
    expect(result.timings.totalMs).toBe(40);
  });

  it("reads config_hash only from the top-level response field", async () => {
    const { config_hash: _omit, ...withoutTop } = okBody;
    mockFetch(async () =>
      new Response(JSON.stringify({ ...withoutTop, meta: { config_hash: "meta-only" } }), { status: 200 }),
    );
    const result = await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID });
    expect(result.retrieval?.configHash).toBeNull();
  });

  it("labels degraded results in the context and metadata (INV-16)", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ ...okBody, degraded: ["rerank_unavailable"] }), { status: 200 }),
    );
    const result = await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID });
    expect(result.retrieval?.degraded).toEqual(["rerank_unavailable"]);
    expect(result.context).toContain("degraded (rerank_unavailable)");
  });

  it("an empty workspace is insufficient evidence with a no-sources message, not a failure", async () => {
    mockFetch(async () =>
      new Response(
        JSON.stringify({
          ...okBody,
          candidates: [],
          context: { entries: [] },
          evidence: { bundle_id: "b", next_label: 1, items: [] },
          answerability: { ...okBody.answerability, state: "INSUFFICIENT_EVIDENCE", reason: "no_sources", top_rerank_score: null },
        }),
        { status: 200 },
      ),
    );
    const result = await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID });
    expect(result.status).toBe("empty");
    expect(result.answerability?.state).toBe("INSUFFICIENT_EVIDENCE");
    expect(result.message).toBe(NO_SOURCES_MESSAGE);
    expect(result.context).toContain("INSUFFICIENT_EVIDENCE");
    expect(result.context).not.toContain("SEARCH_UNAVAILABLE");
  });

  it.each([503, 500, 403])("HTTP %s becomes the unavailable state with no chunks", async (status) => {
    mockFetch(async () => new Response(JSON.stringify({ error_code: "RETRIEVAL_FAILED" }), { status }));
    const result = await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID });
    expect(result.status).toBe("unavailable");
    expect(result.chunks).toEqual([]);
    expect(result.message).toBe(SEARCH_UNAVAILABLE_MESSAGE);
    expect(result.context).toContain("SEARCH_UNAVAILABLE");
  });

  it("network failure and malformed bodies become unavailable", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mockFetch(async () => {
      throw new Error("ECONNREFUSED");
    });
    expect((await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID })).status).toBe("unavailable");
    mockFetch(async () => new Response(JSON.stringify({ nope: true }), { status: 200 }));
    expect((await retrieveViaPython({ query: "q", userId: "u", workspaceId: WID })).status).toBe("unavailable");
  });
});
