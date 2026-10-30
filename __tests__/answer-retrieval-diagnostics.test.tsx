// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UIMessage } from "@ai-sdk/react";
import { getMessageRagResults } from "@/lib/chat-tools";
import { AnswerRetrievalDiagnostics } from "@/components/answer-retrieval-diagnostics";
import { RAG_TOOL_RESULT_TYPE, type RagToolResult } from "@/lib/rag-types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.unstubAllGlobals(); });
const result = (query: string, traceId?: string): RagToolResult => ({
  type: RAG_TOOL_RESULT_TYPE, query, traceId, status: "retrieved", context: "", topK: 1, threshold: 0,
  timings: { embeddingMs: 0, retrievalMs: 0, totalMs: 0 },
  retrieval: { engine: "python", profile: "hybrid", degraded: [], stageMs: { lexical_plus_embed_wait: 12 }, counts: {} },
  answerability: { state: "ANSWERABLE", reason: "sufficient", topRerankScore: 0.8, tau: 0.3, tauCalibrated: false, gateDegraded: false },
  chunks: [{ rank: 1, documentId: 0, fileId: 1, fileName: "secret.pdf", fileType: "", chunkIndex: 0, similarity: 0,
    metadata: null, contentPreview: "", content: "", stageRanks: { dense: 2, lexical: null, fused: 1, reranked: 3 }, selected: true }],
});
function mount(node: React.ReactNode) { host = document.createElement("div"); document.body.append(host); root = createRoot(host); act(() => root.render(node)); return host; }
function expand(el: Element) { act(() => { (el as HTMLDetailsElement).open = true; el.dispatchEvent(new Event("toggle")); }); }
const trace = (usd: number | null) => ({ trace: { id: "t1", cost: { embed_tokens: 2, rerank_tokens: 5, usd, estimated: true, prices_as_of: "2026-01-01" } }, candidates: [{ source_name: "do not show", source_version_id: "private", version_number: 2, dense_rank: null, lexical_rank: null, fusion_rank: null, rerank_rank: null, selected: true, source_unavailable: true }], candidates_truncated: false });

describe("answer retrieval diagnostics", () => {
  it("extracts live and stored search results for each answer, keeping searches separate", () => {
    const first = { parts: [{ type: "tool-searchKnowledgeBase", state: "output-available", output: result("first") }, { type: "tool-invocation", toolName: "searchKnowledgeBase", result: result("second") }] } as unknown as UIMessage;
    const second = { parts: [{ type: "tool-invocation", toolName: "searchKnowledgeBase", result: result("other") }] } as unknown as UIMessage;
    const el = mount(<><AnswerRetrievalDiagnostics results={getMessageRagResults(first)} /><AnswerRetrievalDiagnostics results={getMessageRagResults(second)} /></>);
    const details = el.querySelectorAll("details"); expand(details[0]);
    expect(details[0].textContent).toContain("first"); expect(details[0].textContent).toContain("second"); expect(details[0].textContent).not.toContain("other");
    expect(details[0].textContent).toContain("Lexical + embedding wait: 12"); expect(details[0].textContent).toContain("cutoff 0.3 (uncalibrated)");
    expect(details[0].textContent).toContain("dense 2, lexical unavailable, fused 1, reranked 3");
  });
  it("loads only on request, permits retry, and redacts unavailable sources", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce({ ok: false, status: 500 }).mockResolvedValueOnce({ ok: true, status: 200, json: async () => trace(null) });
    vi.stubGlobal("fetch", fetcher);
    const el = mount(<AnswerRetrievalDiagnostics results={[result("one", "t1")]} />); expand(el.querySelector("details")!);
    expect(fetcher).not.toHaveBeenCalled();
    await act(async () => { el.querySelector("button")!.click(); await Promise.resolve(); });
    expect(el.textContent).toContain("Stored trace could not be loaded");
    await act(async () => { el.querySelector("button")!.click(); await Promise.resolve(); });
    expect(el.textContent).toContain("Retrieval cost: unavailable (estimated)");
    expect(el.textContent).toContain("Source unavailable"); expect(el.textContent).not.toContain("do not show"); expect(el.textContent).not.toContain("private");
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("shows recorded estimated cost and does not retain another search's trace", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => trace(0.002) }));
    const el = mount(<AnswerRetrievalDiagnostics results={[result("one", "t1")]} />); expand(el.querySelector("details")!);
    await act(async () => { el.querySelector("button")!.click(); await Promise.resolve(); });
    expect(el.textContent).toContain("$0.002 (estimated)");
    act(() => root.render(<AnswerRetrievalDiagnostics results={[result("two", "t2")]} />));
    expect(el.textContent).not.toContain("$0.002"); expect(el.textContent).toContain("Load stored trace details");
  });
  it("distinguishes a missing trace from a trace without recorded cost", async () => {
    const withoutCost = { ...trace(null), trace: { id: "t1" } };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({ ok: false, status: 404 }).mockResolvedValueOnce({ ok: true, status: 200, json: async () => withoutCost }));
    const el = mount(<AnswerRetrievalDiagnostics results={[result("one", "t1")]} />); expand(el.querySelector("details")!);
    await act(async () => { el.querySelector("button")!.click(); await Promise.resolve(); });
    expect(el.textContent).toContain("Stored trace not found or unavailable");
    await act(async () => { el.querySelector("button")!.click(); await Promise.resolve(); });
    expect(el.textContent).toContain("Retrieval cost unavailable.");
  });

  it("keeps legacy trace IDs out of the Python diagnostics endpoint", () => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    const legacy = { ...result("old query", "legacy-nanoid"), engine: undefined, retrieval: undefined };
    const el = mount(<AnswerRetrievalDiagnostics results={[legacy]} />);
    expand(el.querySelector("details")!);
    expect(el.textContent).toContain("Search limit:");
    expect(el.textContent).toContain("Stored trace details unavailable.");
    expect(el.querySelector("button")).toBeNull();
    expect(fetcher).not.toHaveBeenCalled();
  });
});
