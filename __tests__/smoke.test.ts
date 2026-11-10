import { describe, expect, it, vi } from "vitest";
import { runSmoke } from "@/scripts/smoke";

const evidenceId = "8c9eb29d-9f39-4f47-a328-1f9f7c6db001";
const sourceId = 15;

function response(body: unknown, status = 200, headers?: HeadersInit) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers });
}

describe("local smoke steps", () => {
  it("uses the real route sequence, treats Python 401 as expected, and confines the session cookie to Next", async () => {
    const requests: Array<{ url: string; cookie: string | null }> = [];
    const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = input instanceof Request ? input.url : input.toString();
      const cookie = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined)).get("cookie");
      requests.push({ url, cookie });
      if (url.endsWith("/api/health")) return response({ ok: true });
      if (url.endsWith("/api/py/health")) return response({ detail: "unauthorized" }, 401);
      if (url.endsWith("/api/sources/reserve")) return response({
        source: { id: sourceId }, uploadPath: "sources/user/15/scribe-smoke.txt", clientToken: "blob-capability",
      });
      if (url.endsWith("/api/sources/process-now")) return response({ ok: true, ticked: true });
      if (url.endsWith("/api/chat")) return response([
        { type: "text-start", id: "text-1" },
        { type: "text-delta", id: "text-1", delta: "It allows 17 days [E1]." },
        { type: "tool-output-available", toolCallId: "call-1", output: { type: "rag_search_result", evidence: { items: [
          { id: evidenceId, label: "E1", source_id: sourceId, source_name: "scribe-smoke.txt" },
        ] } } },
        { type: "text-end", id: "text-1" },
      ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n");
      if (url.endsWith(`/api/evidence/${evidenceId}`)) return response({ itemId: evidenceId, sourceDeleted: false, sourceId, quote: "The policy allows 17 days of annual leave." });
      return response({ error: "unexpected request" }, 404);
    });
    const uploads: Array<{ pathname: string; token: string; file: File }> = [];
    const wait = vi.fn(async () => undefined);
    const steps = await runSmoke({
      baseUrl: "http://localhost:3000",
      sessionToken: "clerk-session-secret",
      fetcher,
      uploader: async (upload) => { uploads.push(upload); },
      wait,
    });

    expect(steps.map((step) => step.ok)).toEqual([true, true, true, true, true]);
    expect(steps[1].detail).toContain("expected 401");
    expect(steps[3].detail).toContain("answer states 17");
    expect(wait).not.toHaveBeenCalled();
    expect(uploads).toHaveLength(1);
    expect(uploads[0].token).toBe("blob-capability");
    expect(uploads[0].file.name).toBe("scribe-smoke.txt");
    expect(requests.find((request) => request.url.endsWith("/api/py/health"))?.cookie).toBeNull();
    expect(requests.find((request) => request.url.endsWith("/api/health"))?.cookie).toBeNull();
    expect(requests.filter((request) => request.url.startsWith("http://localhost:3000") &&
      !request.url.endsWith("/api/health") && !request.url.endsWith("/api/py/health"))
      .every((request) => request.cookie === "__session=clerk-session-secret")).toBe(true);
    expect(requests.some((request) => request.url.includes(evidenceId))).toBe(true);
    expect(requests.some((request) => new URL(request.url).origin !== "http://localhost:3000")).toBe(false);
  });

  it("fails steps on bad health or missing citations and keeps failure detail sanitized", async () => {
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/api/health")) return response({ ok: false }, 503);
      if (url.endsWith("/api/py/health")) return response({}, 401);
      if (url.endsWith("/api/sources/reserve")) return response({ source: { id: 1 }, uploadPath: "p", clientToken: "b" });
      if (url.endsWith("/api/sources/process-now")) return response({ error: "not available" }, 503);
      if (url.endsWith("/api/chat")) return response("data: [DONE]\n");
      return response({}, 404);
    });
    const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "private-token", fetcher,
      uploader: async () => undefined });
    expect(steps[0]).toMatchObject({ ok: false, detail: "failed" });
    expect(steps[2]).toMatchObject({ ok: false, detail: "failed" });
    expect(steps[3]).toMatchObject({ ok: false, detail: "skipped after upload or queue kick failed" });
    expect(steps[4]).toMatchObject({ ok: false, detail: "failed" });
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith("/api/chat"))).toBe(false);
    expect(JSON.stringify(steps)).not.toContain("private-token");
  });

  it("waits after a not-ready answer, then accepts only a cited answer from the uploaded source", async () => {
    let chatCalls = 0;
    const wait = vi.fn(async () => undefined);
    const answer = (text: string, id = evidenceId, citedSourceId = sourceId, name = "scribe-smoke.txt") => [
      { type: "text-start", id: "t" },
      { type: "text-delta", id: "t", delta: text },
      { type: "tool-output-available", toolCallId: "c", output: { type: "rag_search_result", evidence: { items: [
        { id, label: "E1", source_id: citedSourceId, source_name: name },
      ] } } },
      { type: "text-end", id: "t" },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/api/health")) return response({ ok: true });
      if (url.endsWith("/api/py/health")) return response({}, 401);
      if (url.endsWith("/api/sources/reserve")) return response({ source: { id: sourceId }, uploadPath: "p", clientToken: "b" });
      if (url.endsWith("/api/sources/process-now")) return response({ ok: true, ticked: true });
      if (url.endsWith("/api/chat")) {
        chatCalls += 1;
        return response(chatCalls === 1 ? answer("No sources are ready yet.") : answer("It allows 17 days [E1]."));
      }
      if (url.endsWith(`/api/evidence/${evidenceId}`)) return response({ itemId: evidenceId, sourceDeleted: false, sourceId, quote: "17 days of annual leave" });
      return response({}, 404);
    });
    const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "session", fetcher, wait,
      uploader: async () => undefined });
    expect(chatCalls).toBe(2);
    expect(wait).toHaveBeenCalledTimes(1);
    expect(steps[3].ok).toBe(true);
    expect(steps[4].ok).toBe(true);
  });

  it("bounds not-ready retries and rejects citations to another source", async () => {
    let chatCalls = 0;
    const wait = vi.fn(async () => undefined);
    const fetched: string[] = [];
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input.toString();
      fetched.push(url);
      if (url.endsWith("/api/health")) return response({ ok: true });
      if (url.endsWith("/api/py/health")) return response({}, 401);
      if (url.endsWith("/api/sources/reserve")) return response({ source: { id: sourceId }, uploadPath: "p", clientToken: "b" });
      if (url.endsWith("/api/sources/process-now")) return response({ ok: true });
      if (url.endsWith("/api/chat")) {
        chatCalls += 1;
        return response(`data: ${JSON.stringify({ type: "text-delta", id: "t", delta: "17 days [E1]" })}\n\ndata: ${JSON.stringify({
          type: "tool-output-available", toolCallId: "c", output: { type: "rag_search_result", evidence: { items: [
            { id: evidenceId, label: "E1", source_id: 999, source_name: "other.txt" },
          ] } },
        })}\n\ndata: [DONE]\n`);
      }
      return response({}, 404);
    });
    const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "session", fetcher, wait,
      uploader: async () => undefined });
    expect(chatCalls).toBe(4);
    expect(wait).toHaveBeenCalledTimes(3);
    expect(steps[3]).toMatchObject({ ok: false, detail: "failed" });
    expect(steps[4]).toMatchObject({ ok: false, detail: "failed" });
    expect(fetched.some((url) => url.includes("/api/evidence/"))).toBe(false);
  });

  it("stops after the bounded readiness attempts when answers abstain or omit citations", async () => {
    let chatCalls = 0;
    const wait = vi.fn(async () => undefined);
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/api/health")) return response({ ok: true });
      if (url.endsWith("/api/py/health")) return response({}, 401);
      if (url.endsWith("/api/sources/reserve")) return response({ source: { id: sourceId }, uploadPath: "p", clientToken: "b" });
      if (url.endsWith("/api/sources/process-now")) return response({ ok: true, ticked: true });
      if (url.endsWith("/api/chat")) {
        chatCalls += 1;
        return response(`data: ${JSON.stringify({ type: "text-delta", id: "t", delta: "No indexed source is available yet." })}\n\ndata: [DONE]\n`);
      }
      return response({}, 404);
    });
    const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "session", fetcher, wait,
      uploader: async () => undefined });
    expect(chatCalls).toBe(4);
    expect(wait).toHaveBeenCalledTimes(3);
    expect(steps[3]).toMatchObject({ ok: false, detail: "failed" });
    expect(steps[4]).toMatchObject({ ok: false, detail: "failed" });
    expect(fetcher.mock.calls.some(([input]) => String(input).includes("/api/evidence/"))).toBe(false);
  });

  it("rejects an opened response for a different evidence item even when source and quote match", async () => {
    const answer = [
      { type: "text-delta", id: "t", delta: "It allows 17 days [E1]." },
      { type: "tool-output-available", toolCallId: "c", output: { type: "rag_search_result", evidence: { items: [
        { id: evidenceId, label: "E1", source_id: sourceId, source_name: "scribe-smoke.txt" },
      ] } } },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");
    const fetcher = vi.fn(async (input: string | URL | Request) => {
      const url = input instanceof Request ? input.url : input.toString();
      if (url.endsWith("/api/health")) return response({ ok: true });
      if (url.endsWith("/api/py/health")) return response({}, 401);
      if (url.endsWith("/api/sources/reserve")) return response({ source: { id: sourceId }, uploadPath: "p", clientToken: "b" });
      if (url.endsWith("/api/sources/process-now")) return response({ ok: true });
      if (url.endsWith("/api/chat")) return response(answer);
      if (url.endsWith(`/api/evidence/${evidenceId}`)) return response({
        itemId: "a1d6e319-7c02-4e36-a91d-875f1d3cbaaa", sourceDeleted: false, sourceId,
        quote: "The policy allows 17 days of annual leave.",
      });
      return response({}, 404);
    });
    const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "session", fetcher,
      uploader: async () => undefined, wait: async () => undefined });
    expect(steps[3].ok).toBe(true);
    expect(steps[4]).toMatchObject({ ok: false, detail: "failed" });
  });

  describe("lifecycle steps (update and delete)", () => {
    const evidenceV2 = "5d3c1f10-7b7a-4c6e-9a0f-3f6a9d2e7b22";
    const sse = (text: string, id: string, source = sourceId) => [
      { type: "text-delta", id: "t", delta: text },
      { type: "tool-output-available", toolCallId: "c", output: { type: "rag_search_result", evidence: { items: [
        { id, label: "E1", source_id: source, source_name: "scribe-smoke.txt" },
      ] } } },
    ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join("") + "data: [DONE]\n";

    function app(opts: { engine409?: boolean; deletedStillCited?: boolean; deletedQuoteLeak?: boolean } = {}) {
      const state = { version2: false, deleted: false, requests: [] as string[] };
      const fetcher = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        state.requests.push(`${init?.method ?? "GET"} ${new URL(url).pathname}`);
        if (url.endsWith("/api/health")) return response({ ok: true });
        if (url.endsWith("/api/py/health")) return response({}, 401);
        if (url.endsWith("/api/sources/reserve")) return response({ source: { id: sourceId }, uploadPath: "sources/u/15/scribe-smoke.txt", clientToken: "t1" });
        if (url.endsWith("/api/sources/process-now")) return response({ ok: true });
        if (url.endsWith(`/api/sources/${sourceId}/versions`)) {
          if (opts.engine409) return response({ code: "engine_unsupported" }, 409);
          state.version2 = true;
          return response({ ok: true, version: { id: "v2", versionNumber: 2 }, uploadPath: "sources/u/15/v2/scribe-smoke.txt", clientToken: "t2" });
        }
        if (url.endsWith(`/api/sources/${sourceId}`) && init?.method === "DELETE") {
          state.deleted = true;
          return response({ ok: true });
        }
        if (url.endsWith("/api/chat")) {
          if (state.deleted) return response(opts.deletedStillCited ? sse("17 days [E1]", evidenceId) : "data: [DONE]\n");
          return state.version2 ? response(sse("It allows 21 days [E1].", evidenceV2)) : response(sse("It allows 17 days [E1].", evidenceId));
        }
        if (url.endsWith(`/api/evidence/${evidenceId}`)) {
          if (state.deleted) return opts.deletedQuoteLeak
            ? response({ itemId: evidenceId, sourceDeleted: true, quote: "leaked" })
            : response({ itemId: evidenceId, sourceDeleted: true, notice: "deleted" });
          return response({ itemId: evidenceId, sourceDeleted: false, sourceId, quote: "The policy allows 17 days of annual leave." });
        }
        if (url.endsWith(`/api/evidence/${evidenceV2}`)) {
          return state.deleted ? response({ error: { code: "NOT_FOUND" } }, 404)
            : response({ itemId: evidenceV2, sourceDeleted: false, sourceId, versionNumber: 2, quote: "The policy allows 21 days of annual leave." });
        }
        return response({}, 404);
      });
      return { fetcher, state };
    }
    const run = (fetcher: Fetcher, extra: Record<string, unknown> = {}) => runSmoke({
      baseUrl: "http://localhost:3000", sessionToken: "session", fetcher, uploader: async () => undefined, wait: async () => undefined,
      lifecycle: true, ...extra,
    });
    type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;

    it("adds update and delete steps in order, and every one passes against a well-behaved deployment", async () => {
      const { fetcher, state } = app();
      const steps = await run(fetcher);
      expect(steps.map((s) => s.name).slice(5)).toEqual([
        "update: upload version 2", "update: answer reflects version 2", "update: citation opens at version 2",
        "delete: remove the Source", "delete: citation withheld", "delete: answers no longer cite it",
      ]);
      expect(steps.map((s) => s.ok)).toEqual(Array(11).fill(true));
      expect(state.requests).toContain(`DELETE /api/sources/${sourceId}`);
      expect(JSON.stringify(steps)).not.toContain("session");
    });

    it("is off unless requested, so the local run stays five steps", async () => {
      const { fetcher } = app();
      const steps = await runSmoke({ baseUrl: "http://localhost:3000", sessionToken: "session", fetcher, uploader: async () => undefined, wait: async () => undefined });
      expect(steps).toHaveLength(5);
    });

    it("marks update as SKIPPED (not passed) when the deployment is not on the Python engine, and still tests delete", async () => {
      const { fetcher } = app({ engine409: true });
      const steps = await run(fetcher);
      const update = steps.filter((s) => s.name.startsWith("update"));
      expect(update).toHaveLength(3);
      expect(update.every((s) => s.skipped === true && s.ok === false && s.detail.startsWith("SKIPPED"))).toBe(true);
      expect(steps.filter((s) => s.name.startsWith("delete")).every((s) => s.ok)).toBe(true);
    });

    it("treats a 409 that is not the engine flag as a failure, not a skip", async () => {
      const { fetcher } = app();
      const conflicting = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        if (url.endsWith(`/api/sources/${sourceId}/versions`)) return response({ code: "no_active_version" }, 409);
        return fetcher(input, init);
      });
      const steps = await run(conflicting as never);
      const upload = steps.find((s) => s.name === "update: upload version 2")!;
      expect(upload).toMatchObject({ ok: false, detail: "failed" });
      expect(upload.skipped).toBeUndefined();
    });

    it("does not call a delete proven when the citation never opened before it", async () => {
      const { fetcher } = app();
      const neverOpens = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        if (url.includes("/api/evidence/") && init?.method === undefined && url.includes(evidenceV2)) return response({ error: { code: "NOT_FOUND" } }, 404);
        return fetcher(input, init);
      });
      const steps = await run(neverOpens as never);
      expect(steps.find((s) => s.name === "delete: remove the Source")).toMatchObject({ ok: false });
    });

    it("fails when a deleted Source still exposes its quote or is still cited in answers", async () => {
      const leak = await run(app({ deletedQuoteLeak: true }).fetcher);
      // after update the reopened citation is the v2 id, which 404s; the leak is checked on the v1 id when no v2 exists
      const leakNoUpdate = await run(app({ engine409: true, deletedQuoteLeak: true }).fetcher);
      expect(leakNoUpdate.find((s) => s.name === "delete: citation withheld")?.ok).toBe(false);
      expect(leak.find((s) => s.name === "delete: remove the Source")?.ok).toBe(true);
      const cited = await run(app({ deletedStillCited: true }).fetcher);
      expect(cited.find((s) => s.name === "delete: answers no longer cite it")?.ok).toBe(false);
    });

    it("rejects an update whose answer still states the old value", async () => {
      const { fetcher } = app();
      const stale = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = input instanceof Request ? input.url : input.toString();
        if (url.endsWith("/api/chat")) return response(sse("It allows 17 days and 21 days [E1].", evidenceId));
        return fetcher(input, init);
      });
      const steps = await run(stale as never, { attempts: 2 });
      expect(steps.find((s) => s.name === "update: answer reflects version 2")?.ok).toBe(false);
    });
  });
});
