// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ChunksPanel, HistoryPanel, StructurePanel, VersionsPanel, type Loaded } from "@/components/source-detail-panels";
import type { ChunkView, JobHistoryEntry } from "@/lib/files/source-detail";
import type { StructureBlock } from "@/lib/files/source-detail-paging";
import { toVersionView } from "@/lib/ingestion/source-version-state";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });
const page = <T,>(items: T[], nextOffset: number | null = null): Loaded<T> => ({ ok: true, data: { items, limit: 50, offset: 0, nextOffset } });
const q = (sel: string) => host.querySelector(sel);
const qa = (sel: string) => Array.from(host.querySelectorAll(sel));

const block = (i: number, headingPath: string[], over: Partial<StructureBlock> = {}): StructureBlock => ({
  id: `b${i}`, blockIndex: i, type: "paragraph", headingPath, page: 1, ocr: false, rowNumber: null,
  charStart: 0, charEnd: 5, preview: `text ${i}`, textLength: 6, ...over,
});

it("structure: nests by heading path and flags OCR blocks", async () => {
  const load = vi.fn().mockResolvedValue(page([
    block(0, ["Intro"]),
    block(1, ["Intro", "Scope"], { ocr: true, page: 2 }),
    block(2, ["Intro", "Scope"], { page: null, type: "table" }),
  ]));
  await act(async () => root.render(<StructurePanel load={load} />));
  await flush();
  expect(qa('[data-testid="section"]').map((s) => s.getAttribute("data-depth"))).toEqual(["1", "2"]);
  const ocr = qa('[data-testid="ocr-flag"]');
  expect(ocr).toHaveLength(1);
  expect(ocr[0].textContent).toBe("OCR text — verify against original");
  expect(qa('[data-testid="block"]').map((b) => b.getAttribute("data-ocr"))).toEqual(["false", "true", "false"]);
  expect(host.textContent).toContain("page unavailable");
  expect(host.textContent).toContain("p. 2");
});

it("structure: Load more requests the next offset and appends", async () => {
  const load = vi.fn()
    .mockResolvedValueOnce(page([block(0, ["A"])], 50))
    .mockResolvedValueOnce(page([block(1, ["A"])], null));
  await act(async () => root.render(<StructurePanel load={load} />));
  await flush();
  expect(load).toHaveBeenLastCalledWith(0);
  const more = q('[data-testid="load-more"]') as HTMLButtonElement;
  expect(more).not.toBeNull();
  await act(async () => more.click());
  await flush();
  expect(load).toHaveBeenLastCalledWith(50);
  expect(qa('[data-testid="block"]')).toHaveLength(2);
  expect(q('[data-testid="load-more"]')).toBeNull();
});

it("structure: shows 404 and empty states", async () => {
  await act(async () => root.render(<StructurePanel load={async () => ({ ok: false, status: 404, error: "Source not found" })} />));
  await flush();
  expect(q('[data-testid="panel-error"]')?.textContent).toBe("Source not found.");
  await act(async () => root.render(<StructurePanel load={async () => page<StructureBlock>([])} />));
  await flush();
  expect(q('[data-testid="panel-empty"]')).not.toBeNull();
});

const chunk = (over: Partial<ChunkView>): ChunkView => ({
  id: "c", parentId: null, chunkIndex: 0, kind: "child", tokenCount: 120, headingText: "Terms", excerpt: "body", contentLength: 4,
  childCount: null, spans: [{ page: 3, charStart: 10, charEnd: 40 }], ...over,
});

it("chunks: shows token count, heading, spans and page range; reloads for the kind filter", async () => {
  const loader = vi.fn((kind: string) => async () => page<ChunkView>(
    kind === "parent"
      ? [chunk({ id: "p", kind: "parent", chunkIndex: 0, tokenCount: 300, childCount: 2, spans: [] })]
      : [chunk({ id: "k", spans: [{ page: 3, charStart: 10, charEnd: 40 }, { page: 4, charStart: 0, charEnd: 9 }] })]
  ));
  const makeLoader = (kind: string) => loader(kind);
  await act(async () => root.render(<ChunksPanel makeLoader={makeLoader} />));
  await flush();
  const card = q('[data-testid="chunk"]')!;
  expect(card.getAttribute("data-kind")).toBe("child");
  expect(card.textContent).toContain("120 tokens");
  expect(card.textContent).toContain("Terms");
  expect(q('[data-testid="chunk-pages"]')?.textContent).toBe("pp. 3-4");
  expect(qa('[data-testid="chunk-spans"] li').map((l) => l.textContent)).toEqual(["p. 3, chars 10-40", "p. 4, chars 0-9"]);

  const parentsButton = qa("button").find((b) => b.textContent === "Parents") as HTMLButtonElement;
  await act(async () => parentsButton.click());
  await flush();
  expect(loader).toHaveBeenLastCalledWith("parent");
  const parent = q('[data-testid="chunk"]')!;
  expect(parent.getAttribute("data-kind")).toBe("parent");
  expect(parent.textContent).toContain("2 children");
  expect(q('[data-testid="chunk-pages"]')?.textContent).toBe("page unavailable");
});

it("chunks: legacy chunks are the fallback only when there is no structural index", async () => {
  await act(async () =>
    root.render(<ChunksPanel makeLoader={() => async () => page<ChunkView>([])} legacyChunks={[{ id: 1, chunkIndex: 0, content: "old text" }]} />)
  );
  await flush();
  expect(q('[data-testid="legacy-chunks"]')?.textContent).toContain("old text");
});

const job = (over: Partial<JobHistoryEntry>): JobHistoryEntry => ({
  id: 1, versionId: "v", versionNumber: 2, status: "failed", stage: "EMBED", attempts: 3, errorCode: "RATE_LIMITED",
  lastError: "provider said slow down", nextRetryAt: null, startedAt: "2026-01-01T10:00:00Z", finishedAt: null,
  createdAt: "2026-01-01T09:00:00Z", updatedAt: "2026-01-01T10:00:00Z", ...over,
});

it("history: timeline with steps, attempts, error code, OCR note and the re-upload note", async () => {
  const load = async () => page([job({}), job({ id: 2, versionNumber: 1, status: "done", stage: "DONE", attempts: 1, errorCode: null, lastError: null })]);
  await act(async () =>
    root.render(<HistoryPanel load={load} ocrPages={4} reuploadRequiredAt="2026-02-01T00:00:00Z" reuploadReason="Original file is gone" />)
  );
  await flush();
  const jobs = qa('[data-testid="job"]');
  expect(jobs).toHaveLength(2);
  expect(jobs[0].querySelector('[data-testid="job-attempts"]')?.textContent).toBe("Attempts: 3");
  expect(jobs[0].querySelector('[data-testid="job-error-code"]')?.textContent).toBe("RATE_LIMITED");
  expect(jobs[0].querySelector('[data-testid="job-last-error"]')?.textContent).toBe("provider said slow down");
  expect(jobs[0].querySelector('[data-step="EMBED"]')?.getAttribute("data-state")).toBe("failed");
  expect(jobs[0].querySelector('[data-step="PARSE"]')?.getAttribute("data-state")).toBe("done");
  expect(jobs[1].querySelector('[data-step="DONE"]')?.getAttribute("data-state")).toBe("done");
  expect(q('[data-testid="ocr-summary"]')?.textContent).toContain("4 pages were read with OCR");
  expect(q('[data-testid="reupload-note"]')?.textContent).toBe("Re-upload to upgrade");
  expect(host.textContent).toContain("Original file is gone");
});

it("history: no OCR and no re-upload note by default", async () => {
  await act(async () => root.render(<HistoryPanel load={async () => page<JobHistoryEntry>([])} ocrPages={0} />));
  await flush();
  expect(q('[data-testid="ocr-summary"]')?.textContent).toBe("No pages needed OCR.");
  expect(q('[data-testid="reupload-note"]')).toBeNull();
  expect(q('[data-testid="panel-empty"]')).not.toBeNull();
});

it("versions: reuses the T19 history, marks the active version and links originals", async () => {
  const v = (n: number, over = {}) => toVersionView({
    id: `id${n}`, versionNumber: n, sizeBytes: 10, createdAt: "2026-01-01T10:00:00Z", uploadState: "stored", duplicateOf: null,
    duplicateOfNumber: null, isActive: false, isDesired: false, generationStatus: null, jobStatus: null, jobStage: null,
    jobAttempts: 0, errorCode: null, lastError: null, ...over,
  });
  const items = [
    v(3, { duplicateOf: "id2", duplicateOfNumber: 2 }),
    v(2, { isActive: true, generationStatus: "ACTIVE" }),
    v(1, { generationStatus: "SUPERSEDED" }),
  ];
  await act(async () => root.render(<VersionsPanel sourceId={7} load={async () => page(items)} />));
  await flush();
  expect(qa('[data-testid="version-state"]').map((e) => e.textContent)).toEqual(["Duplicate", "Active", "Replaced"]);
  expect(host.textContent).toContain("Same file as v2");
  const link = q('[data-testid="open-original-3"]') as HTMLAnchorElement;
  expect(link.getAttribute("href")).toBe("/api/files/7/view?version=3");
  expect(link.textContent).toContain("shared with the earlier version");
});
