/**
 * T27 / INV-13: private text never reaches logs. A canary sits in document text, queries, quotes, claims and in the
 * MESSAGES of the errors the service and the provider can throw (V8's JSON.parse error even quotes a snippet of the bad
 * input). Every console method is captured while the TypeScript retrieval, evidence, verification and error paths run.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const CANARY = (JSON.parse(readFileSync(path.resolve(__dirname, "../../fixtures/adversarial/manifest.json"), "utf8")) as { canary: string }).canary;
const mocks = vi.hoisted(() => ({ callIntelligence: vi.fn(), resolveWorkspace: vi.fn() }));

vi.mock("@/lib/env", () => ({ env: { RETRIEVAL_ENGINE: "python" }, requireEnv: () => "x" }));
vi.mock("@/lib/intelligence-client", () => ({ callIntelligence: mocks.callIntelligence }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: mocks.resolveWorkspace }));

import { GET as evidenceGET } from "@/app/api/evidence/[id]/route";
import { openEvidence } from "@/lib/evidence/open";
import { readStoredVerifications, requestVerification } from "@/lib/evidence/verification";
import { buildPythonToolResult, retrieveViaPython } from "@/lib/python-retrieval";

const METHODS = ["log", "info", "warn", "error", "debug", "trace"] as const;
let captured: string[] = [];
const writes: string[] = [];

beforeEach(() => {
  captured = [];
  writes.length = 0;
  mocks.callIntelligence.mockReset();
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "ws-1" });
  for (const m of METHODS) {
    vi.spyOn(console, m).mockImplementation((...args: unknown[]) => {
      captured.push(
        args
          .map((a) => (a instanceof Error ? `${a.name}: ${a.message}\n${a.stack ?? ""}` : typeof a === "string" ? a : JSON.stringify(a, (_k, v) => (v instanceof Error ? `${v.name}: ${v.message}` : v))))
          .join(" "),
      );
    });
  }
  vi.spyOn(process.stdout, "write").mockImplementation(((chunk: unknown) => (writes.push(String(chunk)), true)) as never);
  vi.spyOn(process.stderr, "write").mockImplementation(((chunk: unknown) => (writes.push(String(chunk)), true)) as never);
});
afterEach(() => vi.restoreAllMocks());

// V8 quotes up to ~10 characters of a body that fails JSON.parse, so a fragment of the canary counts as a leak too
const FRAGMENT = CANARY.slice(0, 8);
const allLogs = () => [...captured, ...writes].join("\n");
const expectNoLeak = () => {
  expect(allLogs()).not.toContain(CANARY);
  expect(allLogs()).not.toContain(FRAGMENT);
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("no document text in TypeScript logs", () => {
  it("retrieveViaPython: transport error, HTTP error and malformed body all echoing the canary", async () => {
    mocks.callIntelligence.mockRejectedValueOnce(new Error(`fetch failed for ${CANARY}`));
    expect((await retrieveViaPython({ query: `q ${CANARY}`, userId: "u", workspaceId: "w" })).status).toBe("unavailable");
    mocks.callIntelligence.mockResolvedValueOnce(json({ error: `bad ${CANARY}` }, 500));
    expect((await retrieveViaPython({ query: `q ${CANARY}`, userId: "u", workspaceId: "w" })).status).toBe("unavailable");
    mocks.callIntelligence.mockResolvedValueOnce(new Response(`${CANARY} not json`, { status: 200 }));
    expect((await retrieveViaPython({ query: `q ${CANARY}`, userId: "u", workspaceId: "w" })).status).toBe("unavailable");
    expect(captured.length).toBeGreaterThan(0); // positive control: the failure path does log (error name only)
    expectNoLeak();
  });

  it("a successful retrieval of canary text logs nothing about it", async () => {
    mocks.callIntelligence.mockResolvedValueOnce(
      json({
        profile: "hybrid-rrf-rerank",
        candidates: [{ rank: 1, chunk_id: "c1", doc_name: "n.md", content: `The pump warranty ${CANARY}`, source_id: 1 }],
        context: { entries: [{ chunk_id: "c1", text: `The pump warranty ${CANARY}`, doc_name: "n.md", source_id: 1, label: "E1", evidence_item_id: "i1" }] },
        answerability: { state: "ANSWERABLE", reason: "ok", tau: 0, tau_calibrated: false },
        evidence: { bundle_id: "b1", next_label: 2, items: [{ id: "i1", label: "E1", source_id: 1, source_name: "n.md" }] },
      }),
    );
    const r = await retrieveViaPython({ query: `warranty ${CANARY}`, userId: "u", workspaceId: "w" });
    expect(r.status).toBe("retrieved");
    expect(buildPythonToolResult("q", { profile: "p", candidates: [], answerability: { state: "INSUFFICIENT_EVIDENCE", reason: "below_tau" } }, 1).status).toBe("insufficient");
    expectNoLeak();
  });

  it("openEvidence and the evidence route: errors that quote the response body do not reach the logs", async () => {
    mocks.callIntelligence.mockRejectedValueOnce(new Error(`socket hang up ${CANARY}`));
    expect(await openEvidence("22222222-2222-4222-8222-222222222222", { userId: "u", workspaceId: "w" })).toEqual({ ok: false, status: 502 });
    // a body that is not JSON (a proxy page, a truncated response): V8's SyntaxError quotes the start of the text
    mocks.callIntelligence.mockResolvedValueOnce(new Response(`${CANARY} <html>gateway</html>`, { status: 200 }));
    const id = "22222222-2222-4222-8222-222222222222";
    const res = await evidenceGET(new Request(`https://scribe.example/api/evidence/${id}`), { params: Promise.resolve({ id }) });
    expect(res.status).toBeGreaterThanOrEqual(500);
    expect(await res.text()).not.toContain(FRAGMENT);
    expectNoLeak();
  });

  it("verification calls swallow provider, JSON and transport failures without logging them", async () => {
    mocks.callIntelligence.mockRejectedValueOnce(new Error(CANARY));
    expect(await requestVerification({ userId: "u", workspaceId: "w", text: `Fact ${CANARY} [E1].`, results: [] })).toEqual({ state: "unavailable" });
    mocks.callIntelligence.mockResolvedValueOnce(new Response(`${CANARY}`, { status: 200 }));
    expect(await requestVerification({ userId: "u", workspaceId: "w", text: `Fact ${CANARY} [E1].`, results: [] })).toEqual({ state: "unavailable" });
    mocks.callIntelligence.mockResolvedValueOnce(new Response(`${CANARY}`, { status: 200 }));
    expect(await readStoredVerifications({ userId: "u", workspaceId: "w", messageIds: ["m1"] })).toEqual({});
    expectNoLeak();
  });
});
