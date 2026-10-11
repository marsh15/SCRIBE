import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  env: { INGEST_ENGINE: "python" as "python" | "legacy", CRON_SECRET: "c" },
  getUserId: vi.fn(),
  reserveSourceVersion: vi.fn(),
  completeSourceVersionUpload: vi.fn(),
  completeSourceUpload: vi.fn(),
  handleUpload: vi.fn(),
  getBlobMetadata: vi.fn(),
  fireTick: vi.fn(),
  afterCallbacks: [] as Array<() => Promise<void>>,
}));

vi.mock("next/server", async () => {
  const actual = await vi.importActual<typeof import("next/server")>("next/server");
  return { ...actual, after: (cb: () => Promise<void>) => void mocks.afterCallbacks.push(cb) };
});
vi.mock("@/lib/env", () => ({ env: mocks.env, requireEnv: () => "x" }));
vi.mock("@/lib/auth", () => ({
  getUserId: mocks.getUserId,
  isNotAuthenticatedError: (e: unknown) => e instanceof Error && e.message === "not_authenticated",
}));
vi.mock("@/lib/ingestion/source-versions", () => ({
  reserveSourceVersion: mocks.reserveSourceVersion,
  completeSourceVersionUpload: mocks.completeSourceVersionUpload,
}));
vi.mock("@/lib/ingestion/source-intake", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ingestion/source-intake-policy")>(
    "@/lib/ingestion/source-intake-policy"
  );
  const parse = (payload: string) => JSON.parse(payload);
  return {
    SourceIntakeError: actual.SourceIntakeError,
    parseSourceUploadCompletionPayload: parse,
    completeSourceUpload: mocks.completeSourceUpload,
  };
});
vi.mock("@vercel/blob/client", () => ({ handleUpload: mocks.handleUpload }));
vi.mock("@/lib/storage/blob", () => ({ getBlobMetadata: mocks.getBlobMetadata }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: mocks.fireTick }));

import { POST as reserveVersion } from "@/app/api/sources/[id]/versions/route";
import { POST as uploadComplete } from "@/app/api/sources/upload-complete/route";
import { SourceIntakeError } from "@/lib/ingestion/source-intake-policy";

const ctx = (id: string) => ({ params: Promise.resolve({ id }) });
const post = (body: unknown) =>
  new Request("https://scribe.example/api/sources/7/versions", { method: "POST", body: JSON.stringify(body) });
const meta = { name: "notes.md", size: 12, type: "text/markdown" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.afterCallbacks.length = 0;
  mocks.env.INGEST_ENGINE = "python";
  mocks.getUserId.mockResolvedValue("user_1");
});

describe("POST /api/sources/[id]/versions", () => {
  it("reserves the next version for the signed-in user and points the callback at upload-complete", async () => {
    mocks.reserveSourceVersion.mockResolvedValue({
      source: { id: 7 },
      version: { id: "vid", versionNumber: 2 },
      uploadPath: "sources/user_1/7/v2/notes.md",
      clientToken: "tok",
    });
    const res = await reserveVersion(post(meta), ctx("7"));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, version: { versionNumber: 2 }, clientToken: "tok" });
    expect(mocks.reserveSourceVersion).toHaveBeenCalledWith({
      userId: "user_1",
      sourceId: 7,
      metadata: meta,
      callbackUrl: "https://scribe.example/api/sources/upload-complete",
    });
  });

  it("requires authentication", async () => {
    mocks.getUserId.mockRejectedValue(new Error("not_authenticated"));
    expect((await reserveVersion(post(meta), ctx("7"))).status).toBe(401);
    expect(mocks.reserveSourceVersion).not.toHaveBeenCalled();
  });

  it("is refused while the legacy ingestion engine is selected", async () => {
    mocks.env.INGEST_ENGINE = "legacy";
    const res = await reserveVersion(post(meta), ctx("7"));
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("engine_unsupported");
    expect(mocks.reserveSourceVersion).not.toHaveBeenCalled();
  });

  it("maps intake errors (foreign Source 404, no searchable version 409) to their status", async () => {
    mocks.reserveSourceVersion.mockRejectedValueOnce(new SourceIntakeError("Source not found", 404, "source_not_found"));
    expect((await reserveVersion(post(meta), ctx("7"))).status).toBe(404);
    mocks.reserveSourceVersion.mockRejectedValueOnce(new SourceIntakeError("no", 409, "no_active_version"));
    const res = await reserveVersion(post(meta), ctx("7"));
    expect(res.status).toBe(409);
    expect((await res.json()).code).toBe("no_active_version");
  });

  it("answers 503 without leaking details on unexpected failures", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    mocks.reserveSourceVersion.mockRejectedValueOnce(new Error("connection string postgres://secret"));
    const res = await reserveVersion(post(meta), ctx("7"));
    expect(res.status).toBe(503);
    expect(JSON.stringify(await res.json())).not.toContain("secret");
    err.mockRestore();
  });
});

describe("POST /api/sources/upload-complete (version callbacks)", () => {
  async function callback(tokenPayload: object) {
    mocks.handleUpload.mockImplementation(async ({ onUploadCompleted }) => {
      await onUploadCompleted({
        blob: { url: "https://blob/x", pathname: "sources/user_1/7/v2/notes.md" },
        tokenPayload: JSON.stringify(tokenPayload),
      });
      return { type: "blob.upload-completed", response: "ok" };
    });
    mocks.getBlobMetadata.mockResolvedValue({ size: 12, contentType: "text/markdown" });
    return uploadComplete(new Request("https://scribe.example/api/sources/upload-complete", { method: "POST", body: "{}" }));
  }

  it("routes a payload with versionId to the version completion and ticks once when a build was queued", async () => {
    mocks.completeSourceVersionUpload.mockResolvedValue({ queued: true });
    const res = await callback({ sourceId: 7, userId: "user_1", versionId: "vid" });
    expect(res.status).toBe(200);
    expect(mocks.completeSourceVersionUpload).toHaveBeenCalledTimes(1);
    expect(mocks.completeSourceUpload).not.toHaveBeenCalled();
    expect(mocks.completeSourceVersionUpload.mock.calls[0][0]).toMatchObject({
      payload: { sourceId: 7, userId: "user_1", versionId: "vid" },
      blob: { url: "https://blob/x", size: 12 },
    });
    for (const cb of mocks.afterCallbacks.splice(0)) await cb();
    expect(mocks.fireTick).toHaveBeenCalledTimes(1);
  });

  it("does not tick for a duplicate or replayed callback", async () => {
    mocks.completeSourceVersionUpload.mockResolvedValue({ queued: false, duplicate: true });
    await callback({ sourceId: 7, userId: "user_1", versionId: "vid" });
    expect(mocks.afterCallbacks).toHaveLength(0);
  });

  it("keeps first uploads on the original completion path", async () => {
    mocks.completeSourceUpload.mockResolvedValue({ queued: true });
    await callback({ sourceId: 7, userId: "user_1" });
    expect(mocks.completeSourceUpload).toHaveBeenCalledTimes(1);
    expect(mocks.completeSourceVersionUpload).not.toHaveBeenCalled();
  });
});
