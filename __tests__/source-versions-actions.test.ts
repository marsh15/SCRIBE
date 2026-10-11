import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  sql: vi.fn(),
  generateClientToken: vi.fn(),
  resolveWorkspace: vi.fn(),
}));

vi.mock("@/lib/env", () => ({ env: { INGEST_ENGINE: "python" }, requireEnv: () => "blob-token" }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => mocks.sql(strings.join("?"), values),
  db: {},
}));
vi.mock("@vercel/blob/client", () => ({ generateClientTokenFromReadWriteToken: mocks.generateClientToken }));
vi.mock("@/lib/billing/usage", () => ({
  getUsageSummary: async () => ({ allowOverage: true, projectedOverageInr: 0 }),
  recordUsageEvent: vi.fn(),
}));
vi.mock("@/lib/uploads/limits", () => ({
  getUserMaxUploadBytes: async () => ({ maxBytes: 10_000_000, maxMb: 10, planCode: "free" }),
}));
vi.mock("@/lib/storage/blob", () => ({ deleteBlob: vi.fn(), downloadBlobToBuffer: vi.fn() }));
vi.mock("@/lib/workspace", () => ({
  ensurePersonalWorkspace: async () => "22222222-2222-4222-8222-222222222222",
  resolveWorkspace: mocks.resolveWorkspace,
}));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/server", () => ({ after: vi.fn() }));
vi.mock("@/lib/ingestion/source-intake", () => ({ deleteSource: vi.fn() }));
vi.mock("@/lib/ingestion/python-queue", () => ({ requeueFailedSource: vi.fn() }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: vi.fn() }));

import { getSourceVersions } from "@/app/files/actions";
import { reserveSourceVersion } from "@/lib/ingestion/source-versions";

const metadata = { name: "notes v2.md", size: 12, type: "text/markdown" };
const base = { userId: "user_1", sourceId: 7, metadata, callbackUrl: "https://scribe.example/api/sources/upload-complete" };

beforeEach(() => {
  vi.clearAllMocks();
  mocks.generateClientToken.mockResolvedValue("client-token");
  mocks.resolveWorkspace.mockResolvedValue({ userId: "user_1", workspaceId: "22222222-2222-4222-8222-222222222222" });
});

describe("reserveSourceVersion", () => {
  it("reserves through the database function and ties the Blob callback to the new version", async () => {
    mocks.sql.mockResolvedValueOnce([{ out_version_id: "vid-2", out_version_number: 2 }]);
    const reservation = await reserveSourceVersion(base);
    expect(reservation).toMatchObject({
      version: { id: "vid-2", versionNumber: 2 },
      uploadPath: "sources/user_1/7/v2/notes-v2.md",
      clientToken: "client-token",
    });
    const [query, values] = mocks.sql.mock.calls[0];
    expect(query).toContain("scribe_reserve_source_version");
    expect(values).toEqual(["22222222-2222-4222-8222-222222222222", 7, "user_1", "text/markdown", 12]);
    const tokenArgs = mocks.generateClientToken.mock.calls[0][0];
    expect(tokenArgs.addRandomSuffix).toBe(false);
    expect(JSON.parse(tokenArgs.onUploadCompleted.tokenPayload)).toEqual({ sourceId: 7, userId: "user_1", versionId: "vid-2" });
  });

  it.each([
    ["SOURCE_NOT_FOUND", 404, "source_not_found"],
    ["NO_ACTIVE_VERSION", 409, "no_active_version"],
    ["TYPE_MISMATCH", 415, "type_mismatch"],
  ])("maps the database error %s to HTTP %i", async (message, status, code) => {
    mocks.sql.mockRejectedValueOnce(new Error(message));
    await expect(reserveSourceVersion(base)).rejects.toMatchObject({ status, code });
    expect(mocks.generateClientToken).not.toHaveBeenCalled();
  });

  it("closes the reservation when the client token cannot be issued", async () => {
    mocks.sql.mockResolvedValueOnce([{ out_version_id: "vid-2", out_version_number: 2 }]).mockResolvedValueOnce([]);
    mocks.generateClientToken.mockRejectedValueOnce(new Error("blob down"));
    await expect(reserveSourceVersion(base)).rejects.toThrow("blob down");
    expect(mocks.sql.mock.calls[1][0]).toContain("'failed'");
  });

  it("validates input before touching the database", async () => {
    await expect(reserveSourceVersion({ ...base, sourceId: 0 })).rejects.toMatchObject({ code: "invalid_source" });
    await expect(reserveSourceVersion({ ...base, metadata: { ...metadata, type: "image/png" } })).rejects.toMatchObject({
      code: "unsupported_type",
    });
    expect(mocks.sql).not.toHaveBeenCalled();
  });
});

describe("getSourceVersions action", () => {
  it("returns the derived version history for the signed-in workspace only", async () => {
    mocks.sql.mockResolvedValueOnce([
      {
        id: "v2", version_number: 2, size_bytes: "20", created_at: new Date("2026-01-02T00:00:00Z"), upload_state: "stored",
        duplicate_of: null, duplicate_of_number: null, is_active: false, is_desired: true, generation_status: "BUILDING",
        job_status: "processing", job_stage: "EMBED", job_attempts: 1, error_code: null, last_error: null,
      },
      {
        id: "v1", version_number: 1, size_bytes: 10, created_at: "2026-01-01T00:00:00Z", upload_state: "stored",
        duplicate_of: null, duplicate_of_number: null, is_active: true, is_desired: false, generation_status: "ACTIVE",
        job_status: "completed", job_stage: "DONE", job_attempts: 1, error_code: null, last_error: null,
      },
    ]);
    const versions = await getSourceVersions(7);
    expect(versions.map((v) => [v.versionNumber, v.state])).toEqual([[2, "building"], [1, "active"]]);
    expect(versions[0].sizeBytes).toBe(20);
    const [, values] = mocks.sql.mock.calls[0];
    expect(values).toEqual([7, "22222222-2222-4222-8222-222222222222", "user_1"]);
  });

  it("returns nothing for an invalid id or when the lookup fails", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(await getSourceVersions(-1)).toEqual([]);
    expect(mocks.sql).not.toHaveBeenCalled();
    mocks.sql.mockRejectedValueOnce(new Error("db down"));
    expect(await getSourceVersions(7)).toEqual([]);
    err.mockRestore();
  });
});
