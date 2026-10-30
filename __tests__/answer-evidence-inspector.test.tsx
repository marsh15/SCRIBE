// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";
import { CitedMarkdown } from "@/components/cited-markdown";
import { collectEvidenceByLabel } from "@/lib/evidence/citations";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let host: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); host?.remove(); });

function render(node: React.ReactNode) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root!.render(node));
  return host;
}
function expand(details: HTMLDetailsElement) {
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
}

describe("answer evidence inspector", () => {
  it("expands message-scoped sources and claim statuses without mixing answers", () => {
    const first = new Map([["E1", { itemId: "one", label: "E1", sourceName: "First.pdf", page: 3 }]]);
    const second = new Map([["E1", { itemId: "two", label: "E1", sourceName: "Second.pdf", page: null }]]);
    const el = render(<>
      <CitedMarkdown text="First claim [E1]." evidence={first} verification={{ state: "done", claims: [{ text: "First claim [E1].", status: "PARTIAL", labels: ["E1"] }] }} />
      <CitedMarkdown text="Second claim [E1]." evidence={second} />
    </>);
    const details = el.querySelectorAll("details");
    expect(details).toHaveLength(2);
    expect(details[0].querySelector("summary")?.textContent).toContain("Evidence inspector");
    expand(details[0]);
    expect(details[0].textContent).toContain("First.pdf · Page 3");
    expect(details[0].textContent).toContain("Partly supported · [E1]");
    expect(details[0].textContent).not.toContain("Second.pdf");
    expect(details[0].querySelector('button[aria-label*="First.pdf"]')).not.toBeNull();
    expand(details[1]);
    expect(details[1].textContent).toContain("Second.pdf · Page unavailable");
    expect(details[1].textContent).toContain("Verification is unavailable");
  });

  it("describes unavailable and in-progress data without asserting verification", () => {
    const el = render(<CitedMarkdown text="Answer" evidence={new Map()} verification={{ state: "unavailable" }} />);
    expand(el.querySelector("details")!);
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("No evidence is available");
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("Verification unavailable");
  });

  it("shows unavailable information for an empty assistant answer and excludes user messages", () => {
    const el = render(<>
      <CitedMarkdown text="Answer" evidence={new Map()} inspectable />
      <CitedMarkdown text="Prompt" evidence={new Map()} streaming inspectable={false} />
    </>);
    expect(el.querySelectorAll("details")).toHaveLength(1);
    expand(el.querySelector("details")!);
    const panel = el.querySelector('[data-testid="answer-evidence-inspector"]');
    expect(panel?.textContent).toContain("No evidence is available for this answer");
    expect(panel?.textContent).toContain("Verification is unavailable for this answer");
  });

  it("updates an open inspector as streaming evidence and verification arrive", () => {
    const el = render(<CitedMarkdown text="Answer" evidence={new Map()} streaming />);
    expand(el.querySelector("details")!);
    expect(el.textContent).toContain("Evidence is not yet available");
    expect(el.textContent).toContain("Waiting for verification");
    const evidence = new Map([["E1", { itemId: "one", label: "E1", sourceName: "Source.pdf", page: 2 }]]);
    act(() => root!.render(<CitedMarkdown text="Answer [E1]." evidence={evidence} streaming verification={{ state: "verifying" }} />));
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("Source.pdf · Page 2");
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("Verifying citations");
    act(() => root!.render(<CitedMarkdown text="Answer [E1]." evidence={evidence} verification={{ state: "done", claims: [{ text: "Answer [E1].", status: "VERIFIED", labels: ["E1"] }] }} />));
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("Verified · [E1]");
    expect(el.querySelector("details")?.open).toBe(true);
  });

  it("renders evidence from persisted tool invocation parts", () => {
    const evidence = collectEvidenceByLabel([{ type: "tool-invocation", result: {
      type: "rag_search_result", chunks: [], timings: {}, evidence: {
        items: [{ id: "saved", label: "E2", sourceName: "Saved.pdf", pageStart: 4 }],
      },
    } }]);
    const el = render(<CitedMarkdown text="Stored answer [E2]." evidence={evidence} />);
    expand(el.querySelector("details")!);
    expect(el.querySelector('[data-testid="answer-evidence-inspector"]')?.textContent).toContain("Saved.pdf · Page 4");
    expect(el.querySelector('details button[aria-label*="Saved.pdf"]')).not.toBeNull();
  });
});
