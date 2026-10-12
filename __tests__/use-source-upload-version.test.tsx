// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const put = vi.hoisted(() => vi.fn());
vi.mock("@vercel/blob/client", () => ({ put }));

import { useSourceUpload } from "@/hooks/useSourceUpload";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
const probe: { api: ReturnType<typeof useSourceUpload> | null } = { api: null };
function Probe() {
  probe.api = useSourceUpload();
  return null;
}
const api = () => probe.api!;
beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(<Probe />));
  put.mockReset();
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const file = new File(["# v2"], "notes.md", { type: "text/markdown" });

it("reserves a version, uploads privately with the issued token, then asks the queue to run", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(json({ ok: true, version: { id: "vid", versionNumber: 2 }, uploadPath: "sources/u/7/v2/notes.md", clientToken: "tok" }))
    .mockResolvedValueOnce(json({ ok: true, ticked: true }));
  vi.stubGlobal("fetch", fetchMock);
  put.mockResolvedValue({});
  let result: Awaited<ReturnType<ReturnType<typeof api>["uploadNewVersion"]>> | undefined;
  await act(async () => {
    result = await api().uploadNewVersion(7, file);
  });
  expect(result).toEqual({ success: true, sourceId: 7, versionNumber: 2 });
  expect(fetchMock.mock.calls[0][0]).toBe("/api/sources/7/versions");
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "notes.md", size: file.size, type: "text/markdown" });
  expect(put).toHaveBeenCalledWith("sources/u/7/v2/notes.md", file, expect.objectContaining({ access: "private", token: "tok" }));
  expect(fetchMock.mock.calls[1][0]).toBe("/api/sources/process-now");
});

it("surfaces a refused reservation without uploading anything", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(json({ error: "Source not found", code: "source_not_found" }, 404)));
  let result: Awaited<ReturnType<ReturnType<typeof api>["uploadNewVersion"]>> | undefined;
  await act(async () => {
    result = await api().uploadNewVersion(7, file);
  });
  expect(result).toEqual({ success: false, error: "Source not found" });
  expect(put).not.toHaveBeenCalled();
  expect(api().state.status).toBe("error");
});
