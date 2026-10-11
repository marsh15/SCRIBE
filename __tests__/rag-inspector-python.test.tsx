// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

import { RetrievalDetails, RAGInspector } from "@/components/rag-inspector";
import type { RagRetrievalMeta, RagToolChunk } from "@/lib/rag-types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const retrieval: RagRetrievalMeta = {
  engine: "python",
  profile: "hybrid-rerank",
  configHash: "abcdef1234567890",
  degraded: ["rerank_unavailable"],
  stageMs: { dense: 12, lexical_plus_embed_wait: 30, fusion: 1, rerank: 9, pack: 2 },
  counts: { dense: 40, lexical: 40, fused: 55, reranked: 10, returned: 1 },
};
const chunk: RagToolChunk = {
  rank: 1, documentId: 0, fileId: 7, fileName: "Handbook.pdf", fileType: "", chunkIndex: 0,
  similarity: 0, metadata: null, contentPreview: "x", content: "x", section: "Leave policy",
  selected: true, stageRanks: { dense: 2, lexical: 1, fused: 1, reranked: null },
};

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLDivElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  vi.unstubAllGlobals();
});

function mount(node: React.ReactNode) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
  return host;
}

it("renders stage timeline, candidate ranks, profile, hash and degraded badge", () => {
  const el = mount(<RetrievalDetails retrieval={retrieval} chunks={[chunk]} />);
  for (const s of ["dense", "lexical", "fused", "reranked", "selected"]) {
    expect(el.querySelector(`[data-testid="stage-${s}"]`)).not.toBeNull();
  }
  expect(el.querySelector('[data-testid="stage-dense"]')!.textContent).toContain("12ms");
  expect(el.querySelector('[data-testid="stage-lexical"]')!.textContent).toContain("30ms");
  expect(el.querySelector('[data-testid="stage-fused"]')!.textContent).toContain("1ms");
  expect(el.querySelector('[data-testid="stage-reranked"]')!.textContent).toContain("9ms");
  expect(el.querySelector('[data-testid="stage-selected"]')!.textContent).toContain("2ms");
  expect(el.textContent).toContain("hybrid-rerank");
  expect(el.textContent).toContain("cfg abcdef12");
  expect(el.querySelector('[data-testid="degraded-badge"]')!.textContent).toContain("rerank_unavailable");
  const cells = Array.from(el.querySelectorAll("tbody td")).map((c) => c.textContent);
  expect(cells).toContain("Handbook.pdf" + "Leave policy");
  expect(el.textContent).toContain("yes");
});

it("shows no degraded badge for a clean result", () => {
  const el = mount(<RetrievalDetails retrieval={{ ...retrieval, degraded: [] }} chunks={[]} />);
  expect(el.querySelector('[data-testid="degraded-badge"]')).toBeNull();
});

it("shows the search unavailable alert from a stored tool result", () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ trace: null }))));
  const messages = [
    {
      id: "m1", role: "assistant",
      parts: [{
        type: "tool-invocation", toolName: "searchKnowledgeBase", toolCallId: "t",
        result: {
          type: "rag_search_result", query: "q", status: "unavailable", message: "Search unavailable.",
          context: "", timings: { embeddingMs: 0, retrievalMs: 0, totalMs: 0 }, topK: 0, threshold: 0, chunks: [],
        },
      }],
    },
  ] as never;
  const el = mount(<RAGInspector messages={messages} status="ready" />);
  expect(el.querySelector('[role="alert"]')!.textContent).toContain("Search unavailable");
});
