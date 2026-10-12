// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SourceVersionHistory, UploadNewVersionButton } from "@/components/source-version-history";
import { toVersionView, type SourceVersionRow } from "@/lib/ingestion/source-version-state";

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

const row = (over: Partial<SourceVersionRow>): SourceVersionRow => ({
  id: `id-${over.versionNumber ?? 1}`,
  versionNumber: 1,
  sizeBytes: 2048,
  createdAt: "2026-01-01T10:00:00Z",
  uploadState: "stored",
  duplicateOf: null,
  duplicateOfNumber: null,
  isActive: false,
  isDesired: false,
  generationStatus: null,
  jobStatus: null,
  jobStage: null,
  jobAttempts: 0,
  errorCode: null,
  lastError: null,
  ...over,
});

it("lists versions newest first with their states, and calls out duplicates and failures", () => {
  const versions = [
    toVersionView(row({ versionNumber: 4, jobStatus: "processing", generationStatus: "BUILDING", isDesired: true })),
    toVersionView(row({ versionNumber: 3, duplicateOf: "id-1", duplicateOfNumber: 1 })),
    toVersionView(row({ versionNumber: 2, jobStatus: "failed", generationStatus: "FAILED", errorCode: "RATE_LIMITED" })),
    toVersionView(row({ versionNumber: 1, isActive: true, generationStatus: "ACTIVE" })),
  ];
  act(() => root.render(<SourceVersionHistory versions={versions} />));
  const items = [...host.querySelectorAll("li")];
  expect(items.map((li) => li.getAttribute("data-state"))).toEqual(["building", "duplicate", "failed", "active"]);
  expect(items[0].textContent).toContain("v4");
  expect(items[0].textContent).toContain("previous version stays searchable");
  expect(items[1].textContent).toContain("Same file as v1; nothing was re-indexed.");
  expect(items[2].textContent).toContain("RATE_LIMITED");
  expect(items[2].textContent).toContain("previous version is still searchable");
  expect(items[3].querySelector('[data-testid="version-state"]')?.textContent).toBe("Active");
  expect(items[3].textContent).toContain("2.0 KB");
});

it("shows loading and empty states", () => {
  act(() => root.render(<SourceVersionHistory versions={[]} loading />));
  expect(host.querySelector('[data-testid="versions-loading"]')).not.toBeNull();
  act(() => root.render(<SourceVersionHistory versions={[]} />));
  expect(host.querySelector('[data-testid="versions-empty"]')).not.toBeNull();
});

it("Upload new version opens the file picker and hands the chosen file over", () => {
  const onFile = vi.fn();
  act(() => root.render(<UploadNewVersionButton onFile={onFile} />));
  const input = host.querySelector<HTMLInputElement>('[data-testid="new-version-input"]')!;
  const clicked = vi.spyOn(input, "click");
  const button = [...host.querySelectorAll("button")].find((b) => b.textContent?.includes("Upload new version"))!;
  act(() => button.click());
  expect(clicked).toHaveBeenCalledTimes(1);
  const file = new File(["hi"], "v2.md", { type: "text/markdown" });
  Object.defineProperty(input, "files", { value: [file], configurable: true });
  act(() => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
  expect(onFile).toHaveBeenCalledWith(file);
});

it("disables the action while busy", () => {
  act(() => root.render(<UploadNewVersionButton onFile={vi.fn()} busy />));
  expect([...host.querySelectorAll("button")].every((b) => b.disabled)).toBe(true);
});
