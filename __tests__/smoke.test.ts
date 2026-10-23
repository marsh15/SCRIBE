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
});
