// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/env", () => ({ env: { RETRIEVAL_ENGINE: "python" }, requireEnv: () => "x" }));

import { CitedMarkdown } from "@/components/cited-markdown";
import { streamWithVerification } from "@/lib/chat-verify-stream";
import { splitClaims } from "@/lib/evidence/claims";
import {
  annotateClaims,
  bundlesFromResults,
  matchClaims,
  requestVerification,
  verificationPhase,
  type VerificationData,
} from "@/lib/evidence/verification";
import { RAG_TOOL_RESULT_TYPE, type RagToolResult } from "@/lib/rag-types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const ANSWER = `## Answer
Employees receive 25 days of annual leave [E1]. Contractors receive no paid leave.

## Key Points
- Leave accrues monthly [E1][E2].
- Is leave paid?
- Summarized above.`;

describe("claim splitting", () => {
  it("splits sentences and bullets, skips headings, questions and filler", () => {
    const claims = splitClaims(ANSWER);
    expect(claims.map((c) => c.text)).toEqual([
      "Employees receive 25 days of annual leave [E1].",
      "Contractors receive no paid leave.",
      "Leave accrues monthly [E1][E2].",
    ]);
    expect(claims.map((c) => c.labels)).toEqual([["E1"], [], ["E1", "E2"]]);
    expect(ANSWER.slice(claims[0].end - 5, claims[0].end)).toBe("[E1].");
  });

  it("does not treat refusal or insufficient-evidence messages as claims", () => {
    for (const t of [
      "Insufficient evidence: your documents do not contain enough information to answer this question.",
      "I couldn't find sufficient information about this in your documents.",
      "No sources: this workspace has no indexed sources yet. Upload documents first.",
      "Search unavailable. Your documents could not be searched right now, so no answer can be given. Please retry shortly.",
      "## Answer\nYour documents do not cover this topic.\n\n## Key Points\n- Summarized above.",
    ]) {
      expect(splitClaims(t)).toEqual([]);
    }
  });

  it("keeps an unlabelled negative fact as a claim, ignores code and decimals and abbreviations", () => {
    expect(splitClaims("Weekends are not included.").map((c) => c.text)).toEqual(["Weekends are not included."]);
    expect(splitClaims("```\nNot a claim at all here.\n```")).toEqual([]);
    expect(splitClaims("The fee is 3.5 percent, e.g. for small orders [E2].").length).toBe(1);
    expect(splitClaims("1. First rule applies [E1]\n2. Second rule applies [E2]").length).toBe(2);
  });

  it("a labelled short sentence is a claim; a bare table separator is not", () => {
    expect(splitClaims("Yes [E1].").length).toBe(1);
    expect(splitClaims("| a | b |\n|---|---|\n| Leave days | 25 [E1] |").length).toBe(1);
  });
});

function result(): RagToolResult {
  return {
    type: RAG_TOOL_RESULT_TYPE, query: "q", status: "retrieved", context: "", timings: { embeddingMs: 0, retrievalMs: 0, totalMs: 0 },
    topK: 1, threshold: 0, chunks: [],
    evidence: { bundleId: "b1", nextLabel: 2, items: [{ id: "i1", label: "E1", sourceId: 1, sourceName: "H.pdf", sourceVersionId: "v1", pageStart: null, pageEnd: null, headingPath: [] }] },
  } as RagToolResult;
}

describe("requestVerification", () => {
  const input = { userId: "u", workspaceId: "w", messageId: "m1", text: "Leave is 25 days [E1].", results: [result()] };

  it("sends bundles with versions and the claims, scope verify, and maps statuses", async () => {
    const call = vi.fn(async (_p: string, _t: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      expect(body.bundles).toEqual([{ bundle_id: "b1", items: [{ label: "E1", item_id: "i1", source_version_id: "v1" }] }]);
      expect(body.message_id).toBe("m1");
      return new Response(JSON.stringify({ claims: [{ text: body.claims[0].text, status: "VERIFIED", labels: ["E1"] }] }));
    });
    const out = await requestVerification(input, call as never);
    expect(call.mock.calls[0][0]).toBe("/verify");
    expect(call.mock.calls[0][1]).toMatchObject({ scope: "verify", wid: "w" });
    expect(out).toEqual({ state: "done", claims: [{ text: "Leave is 25 days [E1].", status: "VERIFIED", labels: ["E1"] }] });
  });

  it("is unavailable on http error, network error, malformed or mismatched answers, and never verified", async () => {
    for (const call of [
      async () => new Response("no", { status: 503 }),
      async () => { throw new Error("down"); },
      async () => new Response(JSON.stringify({ nope: 1 })),
      async () => new Response(JSON.stringify({ claims: [] })),
    ]) {
      expect(await requestVerification(input, call as never)).toEqual({ state: "unavailable" });
    }
  });

  it("an unknown status from the service degrades to UNVERIFIED, not verified", async () => {
    const call = async () => new Response(JSON.stringify({ claims: [{ text: "Leave is 25 days [E1].", status: "SUPER_VERIFIED" }] }));
    const out = await requestVerification(input, call as never);
    expect(out).toMatchObject({ state: "done", claims: [{ status: "UNVERIFIED" }] });
  });

  it("makes no call when there is no claim", async () => {
    const call = vi.fn();
    expect(await requestVerification({ ...input, text: "## Answer" }, call as never)).toEqual({ state: "done", claims: [] });
    expect(call).not.toHaveBeenCalled();
  });

  it("collects each bundle once", () => {
    expect(bundlesFromResults([result(), result()]).length).toBe(1);
  });
});

describe("phases and matching", () => {
  it("moves generating -> verifying -> final, or unavailable", () => {
    expect(verificationPhase(true, undefined)).toBe("generating");
    expect(verificationPhase(true, { state: "verifying" })).toBe("verifying");
    expect(verificationPhase(false, { state: "done", claims: [] })).toBe("final");
    expect(verificationPhase(false, { state: "unavailable" })).toBe("unavailable");
    expect(verificationPhase(false, undefined)).toBe("none");
  });

  it("matches stored rows by claim text and annotates after each claim", () => {
    const claims = splitClaims(ANSWER);
    const m = matchClaims(claims, [{ text: claims[1].text, status: "UNCITED", labels: [] }]);
    expect([...m.keys()]).toEqual([1]);
    expect(annotateClaims(ANSWER, claims, m)).toContain("no paid leave. [status](#claim-1)");
  });
});

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
});

function render(ui: React.ReactElement) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(ui));
  return host;
}

const EVIDENCE = new Map([["E1", { itemId: "i1", label: "E1", sourceName: "H.pdf", page: null }]]);

describe("badges in CitedMarkdown", () => {
  const text = "Leave is 25 days [E1]. Contractors get none. Maybe more [E1]. Bad one [E1]. Odd [E1].";
  it("renders a text+icon badge per claim and announces completion in a live region", () => {
    const claims = splitClaims(text);
    const data: VerificationData = {
      state: "done",
      claims: ["VERIFIED", "UNCITED", "PARTIAL", "UNSUPPORTED", "INVALID_CITATION"].map((status, i) => ({ text: claims[i].text, status: status as never, labels: [] })),
    };
    const el = render(<CitedMarkdown text={text} evidence={EVIDENCE} verification={data} />);
    const badges = [...el.querySelectorAll('[data-testid="claim-badge"]')];
    expect(badges.map((b) => b.getAttribute("data-status"))).toEqual(["VERIFIED", "UNCITED", "PARTIAL", "UNSUPPORTED", "INVALID_CITATION"]);
    expect(badges.map((b) => b.textContent)).toEqual(["✓Verified", "○Uncited", "◐Partly supported", "✕Unsupported", "!Invalid citation"]);
    const live = el.querySelector('[role="status"]')!;
    expect(live.getAttribute("aria-live")).toBe("polite");
    expect(live.textContent).toBe("Verification complete: 1 verified, 1 uncited, 1 partly supported, 1 unsupported, 1 invalid citation");
  });

  it("while verifying there are no badges and the live region says so", () => {
    const el = render(<CitedMarkdown text={text} evidence={EVIDENCE} verification={{ state: "verifying" }} streaming />);
    expect(el.querySelectorAll('[data-testid="claim-badge"]').length).toBe(0);
    expect(el.querySelector('[role="status"]')!.textContent).toBe("Verifying citations…");
  });

  it("unavailable shows 'Verification unavailable' and never a verified badge", () => {
    const el = render(<CitedMarkdown text={text} evidence={EVIDENCE} verification={{ state: "unavailable" }} />);
    expect(el.textContent).toContain("Verification unavailable");
    expect(el.querySelectorAll('[data-testid="claim-badge"]').length).toBe(0);
    expect(el.textContent).not.toMatch(/Verified/);
  });

  it("legacy messages (no verification) render without badges or a visible status", () => {
    const el = render(<CitedMarkdown text={text} evidence={EVIDENCE} />);
    expect(el.querySelectorAll('[data-testid="claim-badge"]').length).toBe(0);
    expect(el.querySelector('[role="status"]')).toBeNull();
  });
});

async function drain(stream: ReadableStream<unknown>) {
  const out: unknown[] = [];
  const reader = stream.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return out;
    out.push(value);
  }
}

function model(chunks: unknown[]) {
  return new ReadableStream({ start(c) { chunks.forEach((x) => c.enqueue(x)); c.close(); } }) as never;
}

describe("streamWithVerification", () => {
  const textChunks = [{ type: "start" }, { type: "text-start", id: "t" }, { type: "text-delta", id: "t", delta: "Hi" }, { type: "text-end", id: "t" }];

  it("emits the answer, then verifying, then the result with the same part id, then finish", async () => {
    const out = (await drain(streamWithVerification({
      modelStream: model(textChunks),
      finished: Promise.resolve({ text: "Hi there friend.", messageId: "m1" }),
      verify: async () => ({ state: "done", claims: [] }),
    }))) as Array<{ type: string; id?: string; data?: { state: string } }>;
    const types = out.map((c) => c.type);
    expect(types.indexOf("text-delta")).toBeLessThan(types.indexOf("data-verification"));
    const v = out.filter((c) => c.type === "data-verification");
    expect(v.map((c) => c.data!.state)).toEqual(["verifying", "done"]);
    expect(new Set(v.map((c) => c.id)).size).toBe(1);
    expect(types[types.length - 1]).toBe("finish");
  });

  it("a throwing verifier becomes unavailable and the stream still finishes", async () => {
    const out = (await drain(streamWithVerification({
      modelStream: model(textChunks),
      finished: Promise.resolve({ text: "Hi there friend." }),
      verify: async () => { throw new Error("boom"); },
    }))) as Array<{ type: string; data?: { state: string } }>;
    expect(out.filter((c) => c.type === "data-verification").map((c) => c.data!.state)).toEqual(["verifying", "unavailable"]);
    expect(out[out.length - 1].type).toBe("finish");
  });

  it("skips verification for unavailable-search turns and for turns that never finish", async () => {
    const verify = vi.fn();
    const a = (await drain(streamWithVerification({ modelStream: model(textChunks), finished: Promise.resolve({ text: "x y z", skip: true }), verify }))) as Array<{ type: string }>;
    const b = (await drain(streamWithVerification({ modelStream: model(textChunks), finished: new Promise(() => {}), verify, graceMs: 10 }))) as Array<{ type: string }>;
    expect(verify).not.toHaveBeenCalled();
    expect(a.some((c) => c.type === "data-verification") || b.some((c) => c.type === "data-verification")).toBe(false);
    expect(b[b.length - 1].type).toBe("finish");
  });
});
