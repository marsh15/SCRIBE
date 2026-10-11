// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SourceIngestStatus } from "@/components/source-ingest-status";

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

function render(props: Parameters<typeof SourceIngestStatus>[0]) {
  act(() => root.render(<SourceIngestStatus {...props} />));
}

it("shows stage and attempts while indexing, with no Retry button", () => {
  render({ status: "processing", stage: "EMBED", attempts: 2, onRetry: vi.fn() });
  expect(host.textContent).toContain("Embedding passages");
  expect(host.textContent).toContain("Attempts: 2");
  expect(host.querySelector("button")).toBeNull();
});

it("shows the error code and a working Retry button after a failure", () => {
  const onRetry = vi.fn();
  render({ status: "failed", stage: "FAILED", attempts: 1, errorCode: "UNSUPPORTED_FORMAT", lastError: "nope", onRetry });
  expect(host.querySelector('[data-testid="ingest-error-code"]')?.textContent).toBe("UNSUPPORTED_FORMAT");
  const button = host.querySelector("button")!;
  expect(button.textContent).toContain("Retry");
  act(() => button.click());
  expect(onRetry).toHaveBeenCalledTimes(1);
});

it("disables Retry while a retry is in flight and renders nothing for ready Sources", () => {
  render({ status: "failed", errorCode: "RESOURCE_LIMIT", retrying: true, onRetry: vi.fn() });
  expect(host.querySelector("button")!.disabled).toBe(true);
  render({ status: "ready", stage: "DONE", attempts: 1 });
  expect(host.textContent).toBe("");
});
