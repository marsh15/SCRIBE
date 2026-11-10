// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CitedMarkdown } from "@/components/cited-markdown";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let host: HTMLDivElement;
const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false, onchange: null })) as never;
  Element.prototype.scrollIntoView ??= () => {};
});
afterEach(() => { act(() => root?.unmount()); host?.remove(); vi.unstubAllGlobals(); });

function render(node: React.ReactNode) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
  return host;
}
const expand = (details: HTMLDetailsElement) => act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const evidence = new Map([["E1", { itemId: "one", label: "E1", sourceName: "Policy.pdf", page: 3 }]]);
const answer = (extra: Partial<React.ComponentProps<typeof CitedMarkdown>> = {}) => (
  <CitedMarkdown text="Staff get 17 days [E1]." evidence={evidence}
    verification={{ state: "done", claims: [{ text: "Staff get 17 days [E1].", status: "PARTIAL", labels: ["E1", "E9"] }] }} {...extra} />
);
const claimRow = () => host.querySelector('[data-testid="answer-evidence-inspector"] [data-testid="claim-row"]')!;
const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));

describe("claims lead to the exact passage", () => {
  it("makes each cited label of a claim a keyboard-operable control, and says plainly when a label has no stored evidence", () => {
    const el = render(answer());
    expand(el.querySelector("details")!);
    const row = claimRow();
    const button = row.querySelector("button")!;
    expect(button.tagName).toBe("BUTTON");
    expect(button.getAttribute("aria-label")).toContain("Policy.pdf");
    expect(button.getAttribute("aria-label")).toContain("3");
    expect(row.textContent).toContain("[E9]");
    expect(row.textContent).toContain("no stored evidence");
    expect(row.querySelectorAll("button")).toHaveLength(1);  // the unknown label is not a link
  });

  it("opens the cited passage from the claim with the keyboard, moves focus into it, and Escape returns focus", async () => {
    fetchMock.mockImplementation((url: string) => String(url).startsWith("/api/evidence/")
      ? json({ sourceDeleted: false, itemId: "one", label: "E1", sourceId: 7, sourceName: "Policy.docx", sourceVersionId: "v", versionNumber: 2,
        mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", quote: "Staff get 17 days.", headingPath: ["Leave"],
        page: null, ocr: false, ocrNotice: null, spans: [] })
      : json({}, 404));
    const el = render(answer());
    expand(el.querySelector("details")!);
    const button = claimRow().querySelector("button")! as HTMLButtonElement;
    button.focus();
    await act(async () => { button.click(); });
    await flush();
    const dialog = document.querySelector('[role="dialog"]')!;
    expect(dialog).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledWith("/api/evidence/one", expect.objectContaining({ cache: "no-store" }));
    expect(dialog.textContent).toContain("version 2");
    expect(dialog.contains(document.activeElement)).toBe(true);
    await act(async () => { document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    await flush();
    expect(document.querySelector('[role="dialog"]')).toBeNull();
  });
});

describe("the inspector never invents data that was not stored", () => {
  it("lists conflict explanation, verification time and retrieval snapshot cutoff as not recorded, only when expanded", () => {
    const el = render(answer());
    expect(el.textContent).not.toContain("not recorded");
    expand(el.querySelector("details")!);
    const text = el.querySelector('[data-testid="answer-evidence-inspector"]')!.textContent!;
    for (const field of ["Conflict explanation", "Verification time", "Retrieval snapshot cutoff"]) {
      expect(text).toMatch(new RegExp(`${field}[^.]*not recorded`, "i"));
    }
  });
});

describe("citation viewer states", () => {
  async function open(response: () => Promise<Response> | Response | never) {
    fetchMock.mockImplementation(response as never);
    const el = render(answer());
    expand(el.querySelector("details")!);
    const button = claimRow().querySelector("button")! as HTMLButtonElement;
    await act(async () => { button.click(); });
    return () => document.querySelector('[role="dialog"]')!;
  }

  it("shows a loading state while the citation is fetched", async () => {
    const dialog = await open(() => new Promise<Response>(() => {}));
    expect(dialog().textContent).toContain("Opening citation");
  });

  it("shows a failure the reader can act on, and a missing citation as not found", async () => {
    let dialog = await open(() => json({ error: { code: "EVIDENCE_UNAVAILABLE" } }, 502));
    await flush();
    expect(dialog().querySelector('[role="alert"]')?.textContent).toContain("could not be opened right now");
    act(() => root!.unmount()); host.remove();
    dialog = await open(() => json({ error: { code: "NOT_FOUND" } }, 404));
    await flush();
    expect(dialog().querySelector('[role="alert"]')?.textContent).toContain("not found");
    act(() => root!.unmount()); host.remove();
    dialog = await open(() => Promise.reject(new TypeError("network")));
    await flush();
    expect(dialog().querySelector('[role="alert"]')?.textContent).toContain("could not be opened right now");
  });

  it("shows a deleted source as deleted and withholds the quote", async () => {
    const dialog = await open(() => json({ sourceDeleted: true, itemId: "one", label: "E1", notice: "This source was deleted. The passage is no longer available." }));
    await flush();
    expect(dialog().querySelector('[role="status"]')?.textContent).toContain("deleted");
    expect(dialog().querySelector("mark")).toBeNull();
  });
});
