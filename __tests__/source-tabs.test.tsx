// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { SourceTabs } from "@/components/source-tabs";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
const rendered: string[] = [];

function Harness({ initial = "original" }: { initial?: string }) {
  const [active, setActive] = useState(initial);
  const ids = ["original", "structure", "chunks", "versions", "history"];
  return (
    <SourceTabs
      label="Source detail"
      activeId={active}
      onChange={setActive}
      tabs={ids.map((id) => ({
        id,
        label: id[0].toUpperCase() + id.slice(1),
        render: () => {
          rendered.push(id);
          return <p>{`${id} content`}</p>;
        },
      }))}
    />
  );
}

beforeEach(() => {
  rendered.length = 0;
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<Harness />));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const tabs = () => Array.from(host.querySelectorAll<HTMLElement>('[role="tab"]'));
const key = (el: Element, k: string) =>
  act(() => {
    el.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
  });

it("exposes the WAI-ARIA tabs structure", () => {
  const list = host.querySelector('[role="tablist"]')!;
  expect(list.getAttribute("aria-label")).toBe("Source detail");
  expect(tabs().map((t) => t.textContent)).toEqual(["Original", "Structure", "Chunks", "Versions", "History"]);
  expect(tabs().map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false", "false", "false"]);
  // roving tabindex: only the selected tab is tabbable
  expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1]);
  const panels = Array.from(host.querySelectorAll<HTMLElement>('[role="tabpanel"]'));
  expect(panels).toHaveLength(5);
  tabs().forEach((tab, i) => {
    expect(tab.getAttribute("aria-controls")).toBe(panels[i].id);
    expect(panels[i].getAttribute("aria-labelledby")).toBe(tab.id);
  });
  expect(panels.map((p) => p.hidden)).toEqual([false, true, true, true, true]);
});

it("only renders the selected panel (inactive tabs fetch nothing)", () => {
  expect(rendered).toEqual(["original"]);
  expect(host.textContent).toContain("original content");
  expect(host.textContent).not.toContain("structure content");
});

it("moves focus and selection with Left/Right, wrapping at both ends", () => {
  act(() => tabs()[0].focus());
  key(tabs()[0], "ArrowRight");
  expect(document.activeElement).toBe(tabs()[1]);
  expect(tabs()[1].getAttribute("aria-selected")).toBe("true");
  expect(host.textContent).toContain("structure content");
  key(tabs()[1], "ArrowLeft");
  key(tabs()[0], "ArrowLeft"); // wraps to the last tab
  expect(document.activeElement).toBe(tabs()[4]);
  expect(tabs()[4].getAttribute("aria-selected")).toBe("true");
  key(tabs()[4], "ArrowRight"); // wraps to the first
  expect(document.activeElement).toBe(tabs()[0]);
  expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1, -1, -1]);
});

it("Home and End jump to the ends; other keys do nothing", () => {
  act(() => tabs()[0].focus());
  key(tabs()[0], "End");
  expect(document.activeElement).toBe(tabs()[4]);
  key(tabs()[4], "Home");
  expect(document.activeElement).toBe(tabs()[0]);
  key(tabs()[0], "a");
  expect(tabs()[0].getAttribute("aria-selected")).toBe("true");
});

it("selects on click", () => {
  act(() => tabs()[3].click());
  expect(tabs()[3].getAttribute("aria-selected")).toBe("true");
  expect(host.textContent).toContain("versions content");
});

it("gives tabs and panels a visible focus style", () => {
  expect(tabs()[0].className).toMatch(/focus-visible:ring-2/);
  expect(host.querySelector('[role="tabpanel"]')!.className).toMatch(/focus-visible:ring/);
});
