import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  billingEnabled: true,
  sql: vi.fn(),
  findFile: vi.fn(),
  insertUsage: vi.fn(),
}));
vi.mock("pdf-parse", () => ({ default: vi.fn() }));
vi.mock("@/lib/flags", () => ({ flags: { get billingEnabled() { return h.billingEnabled; } } }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => h.sql(strings.join("?"), values),
  db: {
    query: { files: { findFirst: h.findFile } },
    insert: () => ({ values: (value: unknown) => ({ onConflictDoNothing: () => h.insertUsage(value) }) }),
  },
}));
vi.mock("@/lib/workspace", () => ({ ensurePersonalWorkspace: async () => "22222222-2222-4222-8222-222222222222" }));
vi.mock("@/lib/storage/blob", () => ({ deleteBlob: vi.fn(), downloadBlobToBuffer: async () => new Uint8Array([1, 2, 3]) }));

import { completeSourceUpload } from "@/lib/ingestion/source-intake";
import { completeSourceVersionUpload } from "@/lib/ingestion/source-versions";
import { recordUsageEvent, recordUsageEventStrict } from "@/lib/billing/usage";
import { deleteBlob } from "@/lib/storage/blob";
import { sourceUploadPath, sourceVersionUploadPath } from "@/lib/ingestion/source-intake-policy";

const userId = "user_1";
const sourceId = 7;
const url = "https://blob.example/a";
const versionId = "11111111-1111-4111-8111-111111111111";
const source = { id: sourceId, userId, name: "a.md", size: 3, type: "text/markdown", status: "uploading", storageUrl: null as string | null };
const payload = { sourceId, userId };
const blob = { url, pathname: sourceUploadPath(userId, sourceId, source.name), size: 3, contentType: "text/markdown" };

beforeEach(() => {
  vi.clearAllMocks();
  h.billingEnabled = true;
  h.insertUsage.mockResolvedValue(undefined);
});

describe("upload accounting through the real usage adapter", () => {
  it("retries a failed first-upload charge after the queue commit, with a deletion fence", async () => {
    const stored = { ...source, status: "queued", storageUrl: url };
    h.findFile.mockResolvedValueOnce(source).mockResolvedValueOnce(stored);
    h.sql.mockResolvedValueOnce([{ file_id: sourceId }]);
    h.insertUsage.mockRejectedValueOnce(new Error("write failed")).mockResolvedValue(undefined);
    await expect(completeSourceUpload({ payload, blob })).rejects.toThrow("write failed");
    expect(h.sql.mock.calls[0][0]).toMatch(/"deleted_at"\s+IS\s+NULL/i);
    await expect(completeSourceUpload({ payload, blob })).resolves.toMatchObject({ replayed: true, shouldKick: true });
    expect(h.insertUsage).toHaveBeenCalledTimes(2);
    expect(h.insertUsage.mock.calls[0][0].idempotencyKey).toBe(`source:${sourceId}:storage`);
    expect(h.insertUsage.mock.calls[1][0].idempotencyKey).toBe(`source:${sourceId}:storage`);
  });

  it("does not charge or requeue when the source is deleted during completion", async () => {
    h.findFile.mockResolvedValueOnce(source).mockResolvedValueOnce(null);
    h.sql.mockResolvedValueOnce([]);
    const result = await completeSourceUpload({ payload, blob });
    expect(h.sql.mock.calls[0][0]).toMatch(/"deleted_at"\s+IS\s+NULL/i);
    expect(result).toMatchObject({ queued: false, shouldKick: false, orphanDeleted: true });
    expect(h.insertUsage).not.toHaveBeenCalled();
    expect(deleteBlob).toHaveBeenCalledWith(url);
  });

  it("propagates a version charge failure and retries the same idempotency key", async () => {
    const row = { id: versionId, source_id: sourceId, version_number: 2, size_bytes: 3, mime_type: "text/markdown", upload_state: "uploading", storage_key: null, duplicate_of: null };
    h.sql.mockResolvedValueOnce([row]).mockResolvedValueOnce([{ outcome: "queued", out_version_number: 2, out_duplicate_of: null }]).mockResolvedValueOnce([{ ...row, upload_state: "stored", storage_key: url }]);
    h.insertUsage.mockRejectedValueOnce(new Error("write failed")).mockResolvedValue(undefined);
    const input = { payload: { ...payload, versionId }, blob: { ...blob, pathname: sourceVersionUploadPath(userId, sourceId, 2, "a.md") } };
    await expect(completeSourceVersionUpload(input)).rejects.toThrow("write failed");
    await expect(completeSourceVersionUpload(input)).resolves.toMatchObject({ replayed: true, shouldKick: true });
    expect(h.insertUsage).toHaveBeenCalledTimes(2);
    expect(h.insertUsage.mock.calls[0][0].idempotencyKey).toBe(`source:${sourceId}:version:${versionId}:storage`);
    expect(h.insertUsage.mock.calls[1][0].idempotencyKey).toBe(`source:${sourceId}:version:${versionId}:storage`);
  });

  it("keeps the existing best-effort API and skips all writes when billing is disabled", async () => {
    const event = { userId, metric: "storage_gb_day" as const, quantity: 1, unit: "gb_day" as const };
    const log = vi.spyOn(console, "error").mockImplementation(() => undefined);
    h.insertUsage.mockRejectedValue(new Error("write failed"));
    await expect(recordUsageEvent(event)).resolves.toBeUndefined();
    await expect(recordUsageEventStrict(event)).rejects.toThrow("write failed");
    h.billingEnabled = false;
    await expect(recordUsageEventStrict(event)).resolves.toBeUndefined();
    expect(h.insertUsage).toHaveBeenCalledTimes(2);
    log.mockRestore();
  });
});
