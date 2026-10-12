import { beforeEach, describe, expect, it, vi } from "vitest";

const envMock = vi.hoisted(() => ({
  INGEST_ENGINE: "legacy" as "legacy" | "python",
  INTERNAL_SERVICE_SECRET: "test-secret-test-secret-test-secret-32b",
  INTELLIGENCE_BASE_URL: "http://py.test",
  BLOB_READ_WRITE_TOKEN: "blob",
}));
const mocks = vi.hoisted(() => ({
  sqlCalls: 0,
  batches: [] as unknown[][],
  extract: vi.fn(),
  chunk: vi.fn(),
  embed: vi.fn(),
  download: vi.fn(),
  source: {
    id: 7,
    name: "a.md",
    type: "text/markdown",
    userId: "user_1",
    workspaceId: "11111111-1111-4111-8111-111111111111",
    storageUrl: "https://blob/a.md",
    fileData: null,
  } as Record<string, unknown>,
}));

vi.mock("@/lib/env", () => ({
  env: envMock,
  requireEnv: (k: keyof typeof envMock) => envMock[k],
}));
vi.mock("@vercel/blob/client", () => ({ generateClientTokenFromReadWriteToken: vi.fn() }));
vi.mock("@/lib/billing/usage", () => ({ getUsageSummary: vi.fn(), recordUsageEvent: vi.fn() }));
vi.mock("@/lib/uploads/limits", () => ({ getUserMaxUploadBytes: vi.fn() }));
vi.mock("@/lib/ingestion/extract", () => ({ extractTextFromBuffer: mocks.extract }));
vi.mock("@/lib/chunking", () => ({ chunkContent: mocks.chunk }));
vi.mock("@/lib/embeddings", () => ({ generateEmbeddings: mocks.embed }));
vi.mock("@/lib/storage/blob", () => ({ deleteBlob: vi.fn(), downloadBlobToBuffer: mocks.download }));
vi.mock("@/lib/workspace", () => ({ ensurePersonalWorkspace: vi.fn() }));
vi.mock("@/lib/db-compat", () => ({ ensureDocumentsChunkIndexSchema: vi.fn() }));
vi.mock("@/lib/db-config", () => {
  const chain = (tag: string) => {
    const node: Record<string, unknown> = { tag };
    node.set = (v: unknown) => ((node.v = v), node);
    node.values = (v: unknown) => ((node.v = v), node);
    node.where = () => node;
    return node;
  };
  const sql = vi.fn(async () => {
    mocks.sqlCalls += 1;
    // 1 stale uploads, 2 abandoned legacy, 3 recovered, 4 claimed
    return mocks.sqlCalls === 4 ? [{ id: 1, file_id: 7, attempts: 1 }] : [];
  });
  const db = {
    query: { files: { findFirst: vi.fn(async () => mocks.source) } },
    update: vi.fn(() => chain("update")),
    delete: vi.fn(() => chain("delete")),
    insert: vi.fn(() => chain("insert")),
    batch: vi.fn(async (q: unknown[]) => void mocks.batches.push(q)),
  };
  return { db, sql };
});

import { runQueuedSourceIntake } from "@/lib/ingestion/source-intake";

const ok = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

function lastBatchValues() {
  return (mocks.batches.at(-1) as Array<{ v?: Record<string, unknown> }>).map((q) => q.v);
}

beforeEach(() => {
  mocks.sqlCalls = 0;
  mocks.batches.length = 0;
  vi.clearAllMocks();
  mocks.download.mockResolvedValue(Buffer.from("hello"));
  mocks.extract.mockResolvedValue({ extractedText: "hello", numPages: 1 });
  mocks.chunk.mockResolvedValue([{ content: "hello", metadata: { chunkIndex: 0 } }]);
  mocks.embed.mockResolvedValue([[0.1]]);
});

describe("INGEST_ENGINE=legacy (default)", () => {
  it("uses the TypeScript pipeline and never calls the Python service", async () => {
    envMock.INGEST_ENGINE = "legacy";
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const out = await runQueuedSourceIntake(1);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mocks.extract).toHaveBeenCalledOnce();
    expect(mocks.embed).toHaveBeenCalledOnce();
    expect(out.ready).toBe(1);
    expect(lastBatchValues().some((v) => v && v.extractedText === "hello")).toBe(true);
  });

  it("keeps the existing retry policy for a failing legacy extraction", async () => {
    envMock.INGEST_ENGINE = "legacy";
    mocks.extract.mockRejectedValue(new Error("network blip"));
    const out = await runQueuedSourceIntake(1);
    expect(out.retrying).toBe(1);
    mocks.sqlCalls = 0;
    mocks.extract.mockRejectedValue(new Error("No text extracted"));
    const permanent = await runQueuedSourceIntake(1);
    expect(permanent.failed).toBe(1);
  });
});

describe("INGEST_ENGINE=python", () => {
  beforeEach(() => {
    envMock.INGEST_ENGINE = "python";
  });

  it("calls /api/py/ingest/source with an ingest-scoped token and skips the legacy pipeline", async () => {
    const fetchMock = vi.fn(async () =>
      ok({ status: "ACTIVE", generation_id: "g1", chunks: 12, embedded: 10, warnings: [], timings_ms: {} }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const out = await runQueuedSourceIntake(1);
    expect(out.ready).toBe(1);
    expect(mocks.extract).not.toHaveBeenCalled();
    expect(mocks.embed).not.toHaveBeenCalled();
    expect(mocks.download).not.toHaveBeenCalled();

    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://py.test/api/py/ingest/source");
    expect(JSON.parse(init.body as string)).toEqual({ source_id: 7 });
    const token = (new Headers(init.headers).get("authorization") ?? "").replace("Bearer ", "");
    const payload = JSON.parse(Buffer.from(token.split(".")[1], "base64url").toString());
    expect(payload).toMatchObject({
      sub: "user_1",
      wid: "11111111-1111-4111-8111-111111111111",
      scope: "ingest",
      aud: "scribe-intel",
    });
    expect(lastBatchValues().some((v) => v && v.status === "completed")).toBe(true);
  });

  it("treats a 422 as permanent (no retry)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ok({ detail: { error_code: "OCR_NOT_AVAILABLE", message: "scanned pages need OCR (T21)" } }, 422)),
    );
    const out = await runQueuedSourceIntake(1);
    expect(out.failed).toBe(1);
    expect(out.retrying).toBe(0);
    expect(lastBatchValues().some((v) => v && v.processingError === "scanned pages need OCR (T21)")).toBe(true);
  });

  it("retries 5xx responses and network errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ok({ detail: { message: "database unavailable" } }, 503)));
    expect((await runQueuedSourceIntake(1)).retrying).toBe(1);
    mocks.sqlCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    expect((await runQueuedSourceIntake(1)).retrying).toBe(1);
  });

  it("gives up after the maximum attempts", async () => {
    mocks.sqlCalls = 0;
    const sqlMod = await import("@/lib/db-config");
    (sqlMod.sql as unknown as ReturnType<typeof vi.fn>).mockImplementation(async () => {
      mocks.sqlCalls += 1;
      return mocks.sqlCalls === 4 ? [{ id: 1, file_id: 7, attempts: 5 }] : [];
    });
    vi.stubGlobal("fetch", vi.fn(async () => ok({ detail: "boom" }, 500)));
    expect((await runQueuedSourceIntake(1)).failed).toBe(1);
  });
});
