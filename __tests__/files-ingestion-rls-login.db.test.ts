import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { bridgeEnabled, runBatch, type Harness } from "./support/rls-harness";

/**
 * Files, ingestion and version reads/writes under RLS with a REAL non-owner login (scripts/test-chat-rls.py creates the login
 * and a throwaway database; the test skips without them). Two workspaces, A and B, each with a Source. Every exported
 * function that touches files runs as workspace A and must neither see nor change B's rows. After each call the same
 * physical connection is asked for its context, which must be empty (transaction-local context, no leak into the pool).
 */
const enabled = bridgeEnabled("INGEST_RLS");
const WA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const WB = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const h = vi.hoisted(() => ({
  user: "user_a",
  workspace: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
  appUrl: process.env.INGEST_RLS_DATABASE_URL ?? "",
  adminUrl: process.env.INGEST_RLS_ADMIN_URL ?? "",
  contextAfter: "",
  queued: null as Harness["queued"],
  lastColumns: [] as string[][],
  deleteBlob: vi.fn(async () => undefined),
}));

vi.mock("@/lib/workspace", () => ({
  resolveWorkspace: async () => ({ userId: h.user, workspaceId: h.workspace }),
  ensurePersonalWorkspace: async () => h.workspace,
}));
vi.mock("@/lib/auth", () => ({ getUserId: async () => h.user, isNotAuthenticatedError: () => false }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/server", () => ({ after: vi.fn() }));
vi.mock("@/lib/env", () => ({
  env: { INGEST_ENGINE: "legacy", INTERNAL_SERVICE_SECRET: undefined },
  requireEnv: () => "blob-token",
}));
vi.mock("@/lib/storage/blob", () => ({
  deleteBlob: h.deleteBlob,
  downloadBlobToBuffer: async () => new Uint8Array([104, 105]),
  getBlobMetadata: vi.fn(),
}));
vi.mock("@/lib/billing/usage", () => ({
  getUsageSummary: async () => ({ allowOverage: true, projectedOverageInr: 0, planCode: "free" }),
  recordUsageEvent: vi.fn(),
  recordUsageEventStrict: vi.fn(),
}));
vi.mock("@/lib/uploads/limits", () => ({ getUserMaxUploadBytes: async () => ({ maxBytes: 1e9, maxMb: 1000, planCode: "free" }) }));
vi.mock("@vercel/blob/client", () => ({ generateClientTokenFromReadWriteToken: async () => "client-token" }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: vi.fn() }));
vi.mock("@/lib/db-compat", () => ({ ensureDocumentsChunkIndexSchema: vi.fn() }));
vi.mock("@/lib/ingestion/extract", () => ({ extractTextFromBuffer: async () => ({ extractedText: "hello", numPages: 1 }) }));
vi.mock("@/lib/chunking", () => ({ chunkContent: async () => [{ content: "hello", metadata: { chunkIndex: 0 } }] }));
vi.mock("@/lib/embeddings", () => ({ generateEmbeddings: async () => [Array(3072).fill(0.1)] }));
vi.mock("@/lib/db-config", async () => {
  const { createBridgeDb } = await import("./support/rls-harness");
  const db = await createBridgeDb(h as unknown as Harness);
  return {
    db,
    adminDb: db,
    adminSql: () => {
      throw new Error("the owner connection must not be used by a workspace-scoped call");
    },
  };
});

import { getFiles, retrySource, getSourceVersions } from "@/app/files/actions";
import { getFileWithChunks, getSourceChunks, getSourceHistory, getSourceOverview, getSourceStructure, getSourceVersionsPage } from "@/app/files/[id]/actions";
import { GET as viewFile } from "@/app/api/files/[id]/view/route";
import { completeSourceUpload, deleteSource, reserveSourceUpload, runQueuedSourceIntake } from "@/lib/ingestion/source-intake";
import { markSourceDue } from "@/lib/ingestion/python-queue";
import { purgeSoftDeletedSources } from "@/lib/ingestion/source-delete";
import { scoped } from "@/lib/scoped";
import { db } from "@/lib/db-config";
import { sql } from "drizzle-orm";

const admin = (statement: string, params: unknown[] = []) => runBatch(h as unknown as Harness, [{ sql: statement, params }], true)[0];
const as = (user: string, workspace: string) => {
  h.user = user;
  h.workspace = workspace;
};
let fileA = 0;
let fileB = 0;

describe.skipIf(!enabled)("files and ingestion under RLS with a non-owner login", () => {
  beforeAll(() => {
    const role = runBatch(h as unknown as Harness, [{ sql: "select current_user, r.rolsuper, r.rolbypassrls from pg_roles r where r.rolname=current_user", params: [] }])[0][0];
    expect(role.slice(1)).toEqual([false, false]);
    admin("insert into workspaces(id,kind,owner_user_id) values ($1,'personal','user_a'),($2,'personal','user_b')", [WA, WB]);
    fileA = Number(admin("insert into files(name,type,size,user_id,workspace_id,status,storage_url) values ('a.txt','text/plain',2,'user_a',$1,'ready','https://blob.example/a.txt') returning id", [WA])[0][0]);
    fileB = Number(admin("insert into files(name,type,size,user_id,workspace_id,status,storage_url) values ('b.txt','text/plain',2,'user_b',$1,'ready','https://blob.example/b.txt') returning id", [WB])[0][0]);
    admin("insert into ingestion_jobs(file_id,status,attempts) values ($1,'queued',0),($2,'queued',0)", [fileA, fileB]);
    admin("update files set status='failed' where id = $1", [fileB]);
  });
  afterAll(() => {
    admin("delete from files where workspace_id in ($1,$2)", [WA, WB]);
    admin("delete from workspaces where id in ($1,$2)", [WA, WB]);
  });

  it("the database itself hides rows without a context and refuses a write for another workspace", async () => {
    expect(runBatch(h as unknown as Harness, [{ sql: "select id from files", params: [] }])[0]).toEqual([]);
    expect(() => runBatch(h as unknown as Harness, [{ sql: "insert into files(name,type,size,user_id,workspace_id,status) values ('x','t',1,'u',$1,'ready')", params: [WA] }])).toThrow();
    await expect(scoped(WA).sql`insert into files(name,type,size,user_id,workspace_id,status) values ('x','t',1,'u',${WB}::uuid,'ready')`).rejects.toThrow();
    expect(h.contextAfter).toBe("");
  });

  it("lists only the caller's Sources and resets the context", async () => {
    as("user_a", WA);
    expect((await getFiles()).map((f) => f.id)).toEqual([fileA]);
    expect(h.contextAfter).toBe("");
    as("user_b", WB);
    expect((await getFiles()).map((f) => f.id)).toEqual([fileB]);
  });

  it("serves the original only from the caller's workspace (guessed id from another workspace is a 404)", async () => {
    as("user_a", WA);
    const own = await viewFile(new Request(`http://x/api/files/${fileA}/view`), { params: Promise.resolve({ id: String(fileA) }) });
    expect(own.status).toBe(200);
    const foreign = await viewFile(new Request(`http://x/api/files/${fileB}/view`), { params: Promise.resolve({ id: String(fileB) }) });
    expect(foreign.status).toBe(404);
    const foreignVersion = await viewFile(new Request(`http://x/api/files/${fileB}/view?version=1`), { params: Promise.resolve({ id: String(fileB) }) });
    expect(foreignVersion.status).toBe(404);
  });

  it("detail tabs and the legacy chunk view return nothing for a Source in another workspace", async () => {
    as("user_a", WA);
    expect(await getFileWithChunks(fileB)).toBeNull();
    expect((await getFileWithChunks(fileA)) && "file" in (await getFileWithChunks(fileA))!).toBe(true);
    for (const result of [
      await getSourceOverview(fileB),
      await getSourceStructure(fileB),
      await getSourceChunks(fileB),
      await getSourceHistory(fileB),
      await getSourceVersionsPage(fileB),
    ]) {
      expect(result).toEqual({ ok: false, status: 404, error: "Source not found" });
    }
    expect((await getSourceOverview(fileA)).ok).toBe(true);
    expect(await getSourceVersions(fileB)).toEqual([]);
    expect(h.contextAfter).toBe("");
  });

  it("queue helpers cannot touch another workspace's job", async () => {
    as("user_a", WA);
    expect(await markSourceDue(WA, fileB)).toBe(false);
    expect(await retrySource(fileB)).toEqual({ success: false, error: "Only failed Sources can be retried" });
    expect(admin("select status from files where id = $1", [fileB])[0][0]).toBe("failed");
  });

  it("a scoped legacy run claims and indexes only the caller's job", async () => {
    as("user_a", WA);
    const result = await runQueuedSourceIntake(3, { userId: "user_a", workspaceId: WA });
    expect(result.claimed).toBe(1);
    expect(result.ready).toBe(1);
    expect(admin("select status from files where id = $1", [fileA])[0][0]).toBe("ready");
    expect(admin("select count(*) from documents where file_id = $1", [fileA])[0][0]).toBe(1);
    expect(admin("select status from ingestion_jobs where file_id = $1", [fileA])[0][0]).toBe("completed");
    expect(admin("select status, attempts from ingestion_jobs where file_id = $1", [fileB])[0]).toEqual(["queued", 0]);
    expect(h.contextAfter).toBe("");
  });

  it("reserves and completes an upload inside the caller's workspace, and queues nothing for a foreign Source id", async () => {
    as("user_a", WA);
    const reservation = await reserveSourceUpload({ userId: "user_a", metadata: { name: "new.txt", size: 5, type: "text/plain" }, callbackUrl: "http://x/cb" });
    expect(admin("select workspace_id from files where id = $1", [reservation.source.id])[0][0]).toBe(WA);
    const blob = { url: "https://blob.example/new.txt", pathname: reservation.uploadPath, size: 5, contentType: "text/plain" };
    await completeSourceUpload({ payload: { sourceId: reservation.source.id, userId: "user_a" }, blob });
    expect(admin("select status from files where id = $1", [reservation.source.id])[0][0]).toBe("queued");
    expect(admin("select count(*) from ingestion_jobs where file_id = $1", [reservation.source.id])[0][0]).toBe(1);
    // a completion naming another workspace's Source finds nothing to queue: the blob is treated as an orphan
    expect(await completeSourceUpload({ payload: { sourceId: fileB, userId: "user_a" }, blob })).toMatchObject({ queued: false, orphanDeleted: true });
    expect(admin("select status from files where id = $1", [fileB])[0][0]).toBe("failed");
    expect(h.contextAfter).toBe("");
  });

  it("deleting a guessed Source id from another workspace deletes nothing; deleting your own removes it", async () => {
    as("user_a", WA);
    expect(await deleteSource("user_a", fileB)).toBe(false);
    expect(admin("select count(*) from files where id = $1", [fileB])[0][0]).toBe(1);
    expect(await deleteSource("user_a", fileA)).toBe(true);
    expect(admin("select count(*) from files where id = $1", [fileA])[0][0]).toBe(0);
    expect(h.deleteBlob).toHaveBeenCalled();
  });

  it("the purge lists across workspaces only through the definer function and deletes inside each workspace", async () => {
    admin("update files set deleted_at = now() - interval '2 days' where id = $1", [fileB]);
    as("user_a", WA); // the purge runs without any workspace identity
    const listed = (await db.execute(sql`select source_id, workspace_id from app_list_purgeable_sources(0, 10)`)).rows;
    expect(listed.map((r) => (r as { workspace_id: string }).workspace_id)).toContain(WB);
    const summary = await purgeSoftDeletedSources(10);
    expect(summary.purged).toBeGreaterThanOrEqual(1);
    expect(admin("select count(*) from files where id = $1", [fileB])[0][0]).toBe(0);
    expect(h.contextAfter).toBe("");
  });

  it("revoked privileges fail closed", async () => {
    admin("revoke select on files from scribe_app");
    try {
      await expect(scoped(WA).sql`select id from files`).rejects.toThrow();
    } finally {
      admin("grant select on files to scribe_app");
    }
    expect(h.contextAfter).toBe("");
    await expect(scoped(WA).sql`select id from files`).resolves.toBeDefined();
  });
});
