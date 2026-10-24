// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MigrationDashboard } from "@/app/admin/migration/dashboard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const source = (state: string, embeddedChunks: number, progressPct: number | null) => ({
  sourceId: 9,
  name: "policy.pdf",
  state,
  stage: state === "failed" ? "FAILED" : "EMBED",
  embeddedChunks,
  expectedChunks: 4,
  progressPct,
  errorCode: state === "failed" ? "TEMPORARY" : null,
});

const snapshot = (state: string, embeddedChunks: number, progressPct: number | null) => ({
  sources: [source(state, embeddedChunks, progressPct)],
  totals: { sources: 1, embeddedChunks, expectedChunks: 4, progressPct, failures: state === "failed" ? 1 : 0 },
  failures: state === "failed"
    ? [{ sourceId: 9, name: "policy.pdf", stage: "FAILED", errorCode: "TEMPORARY", lastError: "temporary" }]
    : [],
});

let host: HTMLDivElement;
let root: Root;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.useFakeTimers();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(snapshot("failed", 0, 0))))
    .mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(snapshot("building", 1, 25))))
    .mockResolvedValueOnce(new Response(JSON.stringify(snapshot("building", 3, 75))));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("resumes polling after retry and shows later generation progress", async () => {
  await act(async () => {
    root.render(<MigrationDashboard />);
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(host.textContent).toContain("Failures");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await act(async () => {
    host.querySelector("button")!.click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(fetchMock).toHaveBeenCalledTimes(3);
  expect(host.textContent).toContain("1 of 4 passages embedded");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect(host.textContent).toContain("75% · 3 of 4 passages embedded");
});

it("retries a transient progress fetch failure after a short delay", async () => {
  fetchMock.mockReset()
    .mockRejectedValueOnce(new Error("temporary network failure"))
    .mockResolvedValueOnce(new Response(JSON.stringify(snapshot("failed", 0, 0))));
  await act(async () => {
    root.render(<MigrationDashboard />);
    await Promise.resolve();
    await Promise.resolve();
  });
  expect(host.textContent).toContain("Could not load migration progress");

  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("Failures");
});
