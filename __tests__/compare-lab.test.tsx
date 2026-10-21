// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompareLab } from "@/components/compare-lab";
import { COMPARE_PROFILES, degradedLabel, describeMovement, orderedStages } from "@/lib/compare";
import type { CompareResponse, CompareProfileResult, CompareResult } from "@/lib/compare";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const cost = { estimated: true, note: "estimated; 0 for fake adapters", rerank_documents: 0, rerank_tokens: 0, usd: 0 };
const r = (chunk: string, rank: number, extra: Partial<CompareResult> = {}): CompareResult => ({
  rank, chunk_id: chunk, doc_name: `${chunk}.md`, heading_text: null, snippet: `text of ${chunk}`, source_id: 1,
  dense_rank: null, dense_score: null, lexical_rank: null, lexical_score: null, fusion_rank: null, fusion_score: null,
  rerank_rank: null, rerank_score: null, in_context: false, ...extra,
});
const prof = (name: string, results: CompareResult[], degraded: string[] = []): CompareProfileResult => ({
  profile: name, description: "", config_hash: "abcdef0123456789", trace_id: "t", degraded,
  stage_ms: { dense: 7, fusion: 1, total: 12 }, counts: { returned: results.length }, meta: {}, cost, results,
});
const mv = (direction: "same" | "up" | "down" | "new" | "dropped" | "absent", delta: number | null = null) => ({
  direction, delta, places: delta === null ? null : Math.abs(delta),
});

const RESPONSE: CompareResponse = {
  compare_id: "c1", query: "q", limit: 10, profiles: ["dense-only", "hybrid-rrf-rerank"], config_hash: "h",
  snapshot: { id: "10:10:", isolation: "repeatable read", read_only: true, shared_by: ["dense-only", "hybrid-rrf-rerank"] },
  shared: { embedding: { calls: 1, dims: 1536, ms: 3, shared_by: ["dense-only", "hybrid-rrf-rerank"], is_fake: false } },
  profile_results: [
    prof("dense-only", [r("alpha", 1, { dense_rank: 1, dense_score: 0.91 }), r("beta", 2, { dense_rank: 2, dense_score: 0.8 })]),
    prof("hybrid-rrf-rerank", [r("beta", 1, { rerank_rank: 1, rerank_score: 0.7, in_context: true }), r("gamma", 2)], ["rerank_unavailable"]),
  ],
  alignment: {
    baseline: "dense-only",
    rows: [
      { chunk_id: "alpha", doc_name: "alpha.md", heading_text: "Leave", snippet: "text of alpha", source_id: 1, present_in: 1, best_rank: 1, worst_rank: 1, rank_spread: 0,
        ranks: { "dense-only": 1, "hybrid-rrf-rerank": null }, movement: { "dense-only": mv("same", 0), "hybrid-rrf-rerank": mv("dropped") } },
      { chunk_id: "beta", doc_name: "beta.md", heading_text: null, snippet: "text of beta", source_id: 1, present_in: 2, best_rank: 1, worst_rank: 2, rank_spread: 1,
        ranks: { "dense-only": 2, "hybrid-rrf-rerank": 1 }, movement: { "dense-only": mv("same", 0), "hybrid-rrf-rerank": mv("up", 1) } },
      { chunk_id: "gamma", doc_name: "gamma.md", heading_text: null, snippet: "text of gamma", source_id: 1, present_in: 1, best_rank: 2, worst_rank: 2, rank_spread: 0,
        ranks: { "dense-only": null, "hybrid-rrf-rerank": 2 }, movement: { "dense-only": mv("absent"), "hybrid-rrf-rerank": mv("new") } },
    ],
    summary: {
      "dense-only": { same: 2, up: 0, down: 0, new: 0, dropped: 0, returned: 2, overlap_with_baseline: 2, jaccard_with_baseline: 1 },
      "hybrid-rrf-rerank": { same: 0, up: 1, down: 0, new: 1, dropped: 1, returned: 2, overlap_with_baseline: 1, jaccard_with_baseline: 0.33 },
    },
  },
  cost: { estimated: true, note: "estimated; 0 for fake adapters", embed_tokens: 3, embed_usd: 0, rerank_tokens: 0, usd: 0 },
  request_total_ms: 42,
};

let root: ReturnType<typeof createRoot> | null = null;
let host: HTMLDivElement | null = null;
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  vi.unstubAllGlobals();
});

function mount() {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(<CompareLab />));
  return host;
}
const flush = () => act(async () => { await new Promise((res) => setTimeout(res, 0)); });
function type(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new Event("input", { bubbles: true })); });
}
const submit = (el: HTMLElement) => act(async () => { el.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
const button = (el: HTMLElement) => el.querySelector('button[type="submit"]') as HTMLButtonElement;
const box = (el: HTMLElement, name: string) => Array.from(el.querySelectorAll("label")).find((l) => l.textContent?.includes(name))!.querySelector("input") as HTMLInputElement;

describe("lib/compare helpers", () => {
  it("describes movement in words and arrows, never colour alone", () => {
    expect(describeMovement(mv("up", 3), false)).toEqual({ arrow: "↑", text: "up 3 places" });
    expect(describeMovement(mv("up", 1), false).text).toBe("up 1 place");
    expect(describeMovement(mv("down", -2), false)).toEqual({ arrow: "↓", text: "down 2 places" });
    expect(describeMovement(mv("same", 0), false).text).toBe("same rank");
    expect(describeMovement(mv("new"), false).text).toContain("new");
    expect(describeMovement(mv("dropped"), false).text).toContain("dropped");
    expect(describeMovement(mv("same", 0), true).text).toBe("baseline");
  });

  it("labels degraded flags and orders stages", () => {
    expect(degradedLabel("rerank_unavailable")).toContain("fused order");
    expect(degradedLabel("something_new")).toBe("Degraded: something new");
    expect(orderedStages({ total: 9, pack: 1, dense: 4, zzz: 2, fusion: 3 }).map(([k]) => k)).toEqual(["dense", "fusion", "pack", "zzz"]);
  });
});

describe("CompareLab", () => {
  it("needs a query and 2 to 5 profiles before it can run, and caps the selection at five", () => {
    const el = mount();
    expect(button(el).disabled).toBe(true); // no query yet
    type(el.querySelector("input[type=text]") as HTMLInputElement, "leave policy");
    expect(button(el).disabled).toBe(false); // 3 default profiles
    act(() => box(el, "Dense only").click());
    act(() => box(el, "Hybrid (RRF)").click());
    expect(button(el).disabled).toBe(true);
    expect(el.textContent).toContain("1 selected");
    expect(el.textContent).toContain("Select between 2 and 5");
    for (const n of ["Dense only", "Hybrid (RRF)", "Lexical only", "Dense + rerank"]) act(() => box(el, n).click());
    expect(el.textContent).toContain("5 selected");
    const unchecked = Array.from(el.querySelectorAll("fieldset input[type=checkbox]")).filter((i) => !(i as HTMLInputElement).checked);
    expect(unchecked.length).toBe(COMPARE_PROFILES.length - 5); // five selected: every other profile is capped out
    expect(unchecked.every((i) => (i as HTMLInputElement).disabled)).toBe(true);
  });

  it("posts the profiles with the chosen baseline first and renders aligned columns with movement text", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(RESPONSE), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const el = mount();
    // choose exactly dense-only + hybrid-rrf-rerank
    act(() => box(el, "Hybrid (RRF)").click());
    type(el.querySelector("input[type=text]") as HTMLInputElement, "leave policy");
    await submit(el);
    await flush();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/compare");
    expect(JSON.parse(init.body as string)).toEqual({ query: "leave policy", profiles: ["dense-only", "hybrid-rrf-rerank"], limit: 10 });

    const table = el.querySelector("table")!;
    expect(table.querySelector("caption")!.textContent).toContain("Dense only");
    const headers = Array.from(table.querySelectorAll("thead th")).map((h) => h.textContent);
    expect(headers[0]).toBe("Passage");
    expect(headers[1]).toContain("Dense only");
    expect(headers[1]).toContain("baseline");
    expect(headers[2]).toContain("Hybrid + rerank");
    const rows = Array.from(table.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(3);
    const text = (i: number) => rows[i].textContent ?? "";
    expect(text(0)).toContain("alpha.md");
    expect(text(0)).toContain("Rank 1");
    expect(text(0)).toContain("dropped, not in this list");
    expect(text(0)).toContain("Not in top 10");
    expect(text(1)).toContain("up 1 place");
    expect(text(1)).toContain("Rank 2");
    expect(text(1)).toContain("in context");
    expect(text(2)).toContain("new, not in baseline");
    // movement is announced with arrow glyphs that are hidden from assistive tech, and the words carry the meaning
    expect(rows[1].querySelector('[aria-hidden="true"]')!.textContent).toContain("↑");
    // rankers shown separately, no combined score; scores hidden until requested
    expect(text(0)).toContain("dense #1");
    expect(text(0)).not.toContain("0.91");
    act(() => box(el, "Show raw scores").click());
    expect(rows[0].textContent).toContain("dense #1 (0.9100)");
  });

  it("shows per-profile timings, cost, and a degraded status with text and an icon", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(RESPONSE), { status: 200 })));
    const el = mount();
    type(el.querySelector("input[type=text]") as HTMLInputElement, "q");
    await submit(el);
    await flush();
    const degraded = el.querySelector('li[role="status"]')!;
    expect(degraded.textContent).toContain("Degraded: Rerank unavailable: showing fused order");
    expect(degraded.querySelector("svg")).not.toBeNull();
    const cards = Array.from(el.querySelectorAll("section[aria-labelledby^=card-]"));
    expect(cards.length).toBe(2);
    expect(cards[0].textContent).toContain("7 ms");
    expect(cards[0].textContent).toContain("(baseline)");
    expect(cards[0].textContent).toContain("estimated; 0 for fake adapters");
    expect(el.textContent).toContain("Compared 2 profiles in one snapshot");
    expect(el.textContent).toContain("Movement summary");
    expect(el.textContent).toContain("1 up, 0 down, 0 same rank, 1 new, 1 dropped");
  });

  it("explains failures in words instead of showing stale results", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ error: { code: "RETRIEVAL_FAILED", message: "Search is unavailable right now. Please retry shortly." } }), { status: 503 }));
    vi.stubGlobal("fetch", fetchMock);
    const el = mount();
    type(el.querySelector("input[type=text]") as HTMLInputElement, "q");
    await submit(el);
    await flush();
    expect(el.querySelector('[role="status"]')!.textContent).toContain("Search is unavailable right now");
    expect(el.querySelector("table")).toBeNull();
    fetchMock.mockResolvedValueOnce(new Response("{}", { status: 401 }));
    await submit(el);
    await flush();
    expect(el.textContent).toContain("Sign in to compare profiles.");
  });

  it("says so when no profile returned anything", async () => {
    const empty = { ...RESPONSE, alignment: { ...RESPONSE.alignment, rows: [] } };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(empty), { status: 200 })));
    const el = mount();
    type(el.querySelector("input[type=text]") as HTMLInputElement, "q");
    await submit(el);
    await flush();
    expect(el.textContent).toContain("No profile returned any passage");
    expect(el.querySelector("table")).toBeNull();
  });
});
