import { execFileSync } from "node:child_process";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

// A dedicated local test database and a real non-owner LOGIN role; never use a deployment URL.
const appUrl = process.env.FILES_RLS_DATABASE_URL;
const adminUrl = process.env.FILES_RLS_ADMIN_URL;
const localTestUrl = (url: string) => ["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname) && new URL(url).pathname.endsWith("_test");
const enabled = !!appUrl && !!adminUrl && localTestUrl(appUrl) && localTestUrl(adminUrl) && new URL(appUrl).pathname === new URL(adminUrl).pathname;
const workspaceA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const workspaceB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const h = vi.hoisted(() => ({ user: "user_a", workspace: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", contextAfter: "", deleteBlob: vi.fn(), run: (_queries: { sql: string; params: unknown[] }[], _admin = false): BridgeResult[] => [], queued: null as null | Array<{ sql: string; params: unknown[]; resolve: (result: { rows: unknown[] }) => void; reject: (err: Error) => void }> }));
vi.mock("@/lib/auth", () => ({ getUserId: async () => h.user, isNotAuthenticatedError: () => false }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: async () => ({ userId: h.user, workspaceId: h.workspace }), ensurePersonalWorkspace: async () => h.workspace }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: { INGEST_ENGINE: "python" }, requireEnv: () => "blob-token" }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: vi.fn() }));
vi.mock("@/lib/ingestion/source-intake", () => ({ deleteSource: vi.fn() }));
vi.mock("@/lib/billing/usage", () => ({ getUsageSummary: vi.fn(), recordUsageEvent: vi.fn(), recordUsageEventStrict: vi.fn() }));
vi.mock("@/lib/uploads/limits", () => ({ getUserMaxUploadBytes: vi.fn() }));
vi.mock("@/lib/storage/blob", () => ({ deleteBlob: h.deleteBlob, downloadBlobToBuffer: vi.fn() }));
vi.mock("@/lib/db-config", async () => {
  const { drizzle } = await import("drizzle-orm/pg-proxy");
  const schema = await import("@/lib/db-schema");
  const { PgRaw } = await import("drizzle-orm/pg-core/query-builders/raw");
  // Tripwire: the neon tagged-template client must not be reached by the migrated functions.
  const sql = () => { throw new Error("neon sql used by a migrated function"); };
  const db = drizzle(async (sqlText, params) => {
    if (!h.queued) return { rows: h.run([{ sql: sqlText, params }])[0].rows };
    return new Promise((resolve, reject) => h.queued!.push({ sql: sqlText, params, resolve, reject }));
  }, { schema });
  Object.assign(db, { batch: async (queries: PromiseLike<unknown>[]) => {
    const pending: NonNullable<typeof h.queued> = [];
    h.queued = pending;
    const promises = queries.map((q) => Promise.resolve(q));
    await new Promise((resolve) => setImmediate(resolve));
    h.queued = null;
    try {
      const results = h.run(pending);
      pending.forEach((q, i) => {
        // pg-proxy hands the drizzle mapper positional rows; the same array also carries name-keyed rows for raw items.
        const { rows, columns } = results[i];
        Object.defineProperty(rows, "objects", { enumerable: false, value: rows.map((row) => Object.fromEntries(columns.map((c, j) => [c, row[j]]))) });
        q.resolve({ rows });
      });
    } catch (error) {
      pending.forEach((q) => q.reject(error as Error));
    }
    // neon-http returns raw db.execute() results as the full result object ({ rows: name-keyed }); pg-proxy returns bare rows.
    // Rebuild that shape for raw items so the code under test sees what it gets from Neon.
    return Promise.all(promises).then((rs) => rs.map((r, i) => (queries[i] instanceof PgRaw ? { rows: (r as unknown as { objects: unknown[] }).objects } : r)));
  }});
  return { db, sql };
});
import { getFiles } from "@/app/files/actions";
import { GET } from "@/app/api/files/[id]/view/route";
import { getOcrSummary, getSourceHeader, listChunks, listIngestionHistory, listStructure, resolveVersionBlob } from "@/lib/files/source-detail";
import { hardDeleteSource } from "@/lib/ingestion/source-delete";
import { markSourceDue } from "@/lib/ingestion/python-queue";
import { completeSourceVersionUpload } from "@/lib/ingestion/source-versions";
import { sourceVersionUploadPath } from "@/lib/ingestion/source-intake-policy";
import { scoped } from "@/lib/scoped";
import { db } from "@/lib/db-config";
import { sql as drizzleSql } from "drizzle-orm";

// Same transaction bridge as __tests__/support/pg_parameterized.py, plus column names so raw rows can be keyed like Neon's.
const PG_BRIDGE = `
import json, re, sys
import psycopg
payload = json.load(sys.stdin)
try:
    with psycopg.connect(payload["dsn"], autocommit=True) as conn:
        results = []
        error = None
        try:
            with conn.transaction():
                for query in payload["queries"]:
                    ordered = []
                    def param(match):
                        ordered.append(query["params"][int(match.group(1)) - 1])
                        return "%s"
                    statement = re.sub(r"\\$(\\d+)", param, query["sql"])
                    with conn.cursor() as cursor:
                        cursor.execute(statement, ordered)
                        columns = [c.name for c in cursor.description] if cursor.description else []
                        results.append({"rows": cursor.fetchall() if cursor.description else [], "columns": columns})
        except psycopg.Error:
            error = "test transaction rejected"
        # Reuse the same physical connection after commit/rollback: a session-scoped context would leak here.
        context_after = conn.execute("select coalesce(current_setting('app.workspace_id', true), '')").fetchone()[0]
    print(json.dumps({"results": results, "error": error, "context_after": context_after}, default=str))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(1)
`;
type BridgeResult = { rows: unknown[][]; columns: string[] };
h.run = (queries, admin = false) => {
  const input = JSON.stringify({ dsn: admin ? adminUrl : appUrl, queries });
  const output = execFileSync("intelligence/.venv/bin/python", ["-c", PG_BRIDGE], { input, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  const result = JSON.parse(output);
  h.contextAfter = result.context_after;
  if (result.error) throw new Error(result.error);
  return result.results as BridgeResult[];
};
const run = (statement: string, params: unknown[] = [], admin = false) => h.run([{ sql: statement, params }], admin)[0].rows;

describe.skipIf(!enabled)("files, source reads and version completion under RLS with a non-owner login", () => {
  let aSource = 0;
  let bSource = 0;
  const aVersion = "a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1";
  const blobUrl = "https://blob.example/a-source.txt";
  let blobPath = "";

  beforeAll(() => {
    const role = run("select current_user, r.rolsuper, r.rolbypassrls, (select tableowner = current_user from pg_tables where schemaname='public' and tablename='files') from pg_roles r where r.rolname=current_user")[0];
    expect(role).toEqual([new URL(appUrl!).username, false, false, false]);
    run("insert into workspaces(id,kind,owner_user_id) values ($1,'personal','user_a'),($2,'personal','user_b')", [workspaceA, workspaceB], true);
    aSource = Number(run("insert into files(name,type,size,user_id,workspace_id,status,file_data) values ('a-source.txt','text/plain',5,'user_a',$1,'ready','data:text/plain;base64,aGVsbG8=') returning id", [workspaceA], true)[0][0]);
    bSource = Number(run("insert into files(name,type,size,user_id,workspace_id,status,file_data) values ('b-source.txt','text/plain',5,'user_b',$1,'ready','data:text/plain;base64,aGVsbG8=') returning id", [workspaceB], true)[0][0]);
    run("insert into ingestion_jobs(file_id,status,next_retry_at) values ($1,'queued', now() + interval '1 hour'), ($2,'queued', now() + interval '1 hour')", [aSource, bSource], true);
    run("insert into source_versions(id,source_id,workspace_id,version_number,storage_key,size_bytes,mime_type,upload_state) values ($1,$2,$3,1,$4,5,'text/plain','stored')", [aVersion, aSource, workspaceA, blobUrl], true);
    blobPath = sourceVersionUploadPath("user_a", aSource, 1, "a-source.txt");
  });
  // Extra Sources for the cases at the end of this file, seeded as admin: active, stored, one block, one queued job.
  const seedSource = (name: string, wid: string, owner: string) => {
    const fid = Number(run("insert into files(name,type,size,user_id,workspace_id,status,file_data) values ($1,'text/plain',5,$2,$3,'ready','data:text/plain;base64,aGVsbG8=') returning id", [name, owner, wid], true)[0][0]);
    const vid = String(run("insert into source_versions(source_id,workspace_id,version_number,storage_key,size_bytes,mime_type,upload_state) values ($1,$2,1,$3,5,'text/plain','stored') returning id", [fid, wid, blobUrl], true)[0][0]);
    run("update files set active_version_id = $1 where id = $2", [vid, fid], true);
    run("insert into document_blocks(source_version_id,workspace_id,block_index,type,text,heading_path,page,ocr) values ($1,$2,0,'paragraph','hello world',array['Intro'],1,false)", [vid, wid], true);
    run("insert into ingestion_jobs(file_id,source_version_id,status,next_retry_at) values ($1,$2,'queued', now() + interval '1 hour')", [fid, vid], true);
    return { fid, vid, name };
  };
  const foreignPairs = [[workspaceB, "user_b"], [workspaceA, "user_b"], [workspaceB, "user_a"]] as const;

  afterAll(() => {
    run("delete from ingestion_jobs where file_id in (select id from files where workspace_id in ($1,$2))", [workspaceA, workspaceB], true);
    run("delete from source_versions where workspace_id in ($1,$2)", [workspaceA, workspaceB], true);
    run("delete from files where workspace_id in ($1,$2)", [workspaceA, workspaceB], true);
    run("delete from workspaces where id in ($1,$2)", [workspaceA, workspaceB], true);
  });

  it("the restricted login sees nothing without a context, and RLS alone hides a foreign row even when the predicate is dropped", () => {
    expect(run("select id from files")).toEqual([]);
    // Only `id = $1` in the statement: any row returned for workspace B would be RLS, not a workspace_id predicate.
    const out = h.run([
      { sql: "select set_config('app.workspace_id', $1, true)", params: [workspaceB] },
      { sql: "select id from files where id = $1", params: [aSource] },
    ]);
    expect(out[1].rows).toEqual([]);
    expect(h.contextAfter).toBe("");
  }, 60_000);

  it("getSourceHeader returns the Source for its own workspace and user and nothing for a foreign workspace", async () => {
    expect(await getSourceHeader(workspaceA, "user_a", aSource)).toMatchObject({ id: aSource, name: "a-source.txt", status: "ready" });
    expect(h.contextAfter).toBe("");
    expect(await getSourceHeader(workspaceB, "user_b", aSource)).toBeNull();
    expect(await getSourceHeader(workspaceA, "user_b", aSource)).toBeNull();
    expect(await getSourceHeader(workspaceB, "user_a", aSource)).toBeNull();
    expect(h.contextAfter).toBe("");
  }, 60_000);

  it("markSourceDue only touches a job in the caller's own workspace", async () => {
    expect(await markSourceDue(workspaceB, aSource)).toBe(false);
    expect(await markSourceDue(workspaceA, bSource)).toBe(false);
    expect(run("select next_retry_at is null from ingestion_jobs where file_id = $1", [aSource], true)[0][0]).toBe(false);
    expect(run("select next_retry_at is null from ingestion_jobs where file_id = $1", [bSource], true)[0][0]).toBe(false);
    expect(await markSourceDue(workspaceA, aSource)).toBe(true);
    expect(run("select next_retry_at is null from ingestion_jobs where file_id = $1", [aSource], true)[0][0]).toBe(true);
    expect(run("select next_retry_at is null from ingestion_jobs where file_id = $1", [bSource], true)[0][0]).toBe(false);
    expect(h.contextAfter).toBe("");
  }, 60_000);

  it("getFiles lists only the signed-in caller's Sources in the active workspace", async () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    h.user = "user_a"; h.workspace = workspaceA;
    expect((await getFiles()).map((f) => f.name)).toEqual(["a-source.txt"]);
    h.user = "user_b"; h.workspace = workspaceB;
    expect((await getFiles()).map((f) => f.name)).toEqual(["b-source.txt"]);
    h.user = "user_a"; h.workspace = workspaceB;
    expect(await getFiles()).toEqual([]);
    // getFiles swallows errors, so an empty array alone would not prove the scoped query ran.
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
    expect(h.contextAfter).toBe("");
    h.user = "user_a"; h.workspace = workspaceA;
  }, 60_000);

  it("the view route serves the caller's own Source and 404s for a foreign one", async () => {
    const get = (id: number) => GET(new Request(`https://scribe.example/api/files/${id}/view`), { params: Promise.resolve({ id: String(id) }) });
    h.user = "user_a"; h.workspace = workspaceA;
    const own = await get(aSource);
    expect(own.status).toBe(200);
    expect(await own.text()).toBe("hello");
    expect(h.contextAfter).toBe("");
    h.user = "user_b"; h.workspace = workspaceB;
    expect((await get(aSource)).status).toBe(404);
    h.user = "user_a"; h.workspace = workspaceB;
    expect((await get(aSource)).status).toBe(404);
    h.user = "user_a"; h.workspace = workspaceA;
  }, 60_000);

  it("loadVersion finds a version only in its own workspace; a foreign completion is an orphan", async () => {
    const payload = (userId: string) => ({ payload: { sourceId: aSource, userId, versionId: aVersion }, blob: { url: blobUrl, pathname: blobPath, size: 5, contentType: "text/plain" } });
    h.workspace = workspaceA;
    const own = await completeSourceVersionUpload(payload("user_a"));
    expect(own).toMatchObject({ replayed: true, versionNumber: 1 });
    expect(h.contextAfter).toBe("");
    h.workspace = workspaceB;
    const foreign = await completeSourceVersionUpload(payload("user_b"));
    expect(foreign).toMatchObject({ orphanDeleted: true, replayed: false });
    expect(h.deleteBlob).toHaveBeenCalledWith(blobUrl);
    expect(run("select upload_state from source_versions where id = $1", [aVersion], true)).toEqual([["stored"]]);
    expect(h.contextAfter).toBe("");
    h.workspace = workspaceA;
  }, 60_000);

  it("scoped() does not leak context to the next use of the pooled connection (negative control: a session-level set_config does)", async () => {
    await scoped(workspaceA).batch([db.execute(drizzleSql`select 1`)]);
    expect(h.contextAfter).toBe("");
    expect(run("select current_setting('app.workspace_id', true)")[0][0]).toBeNull();
    // Control: prove the detector sees a session-level context that outlives the transaction.
    h.run([{ sql: "select set_config('app.workspace_id', $1, false)", params: [workspaceB] }]);
    expect(h.contextAfter).toBe(workspaceB);
  }, 60_000);

  it("listStructure, listIngestionHistory and getOcrSummary serve the owner's Source and return null for a foreign workspace or user", async () => {
    const seeded = seedSource("c-reads.txt", workspaceA, "user_a");
    expect((await listStructure(workspaceA, "user_a", seeded.fid))?.items).toMatchObject([
      { blockIndex: 0, type: "paragraph", headingPath: ["Intro"], preview: "hello world", textLength: 11, ocr: false },
    ]);
    expect((await listIngestionHistory(workspaceA, "user_a", seeded.fid))?.items).toMatchObject([
      { status: "queued", versionId: seeded.vid, versionNumber: 1 },
    ]);
    expect(await getOcrSummary(workspaceA, "user_a", seeded.fid)).toEqual({ pages: 0, blocks: 0 });
    run("update document_blocks set ocr = true where source_version_id = $1", [seeded.vid], true);
    expect(await getOcrSummary(workspaceA, "user_a", seeded.fid)).toEqual({ pages: 1, blocks: 1 });
    expect(h.contextAfter).toBe("");
    for (const [wid, uid] of foreignPairs) {
      expect(await listStructure(wid, uid, seeded.fid)).toBeNull();
      expect(await listIngestionHistory(wid, uid, seeded.fid)).toBeNull();
      expect(await getOcrSummary(wid, uid, seeded.fid)).toBeNull();
    }
    expect(h.contextAfter).toBe("");
  }, 60_000);

  it("listChunks and resolveVersionBlob serve the owner's Source and return null for a foreign workspace or user", async () => {
    const seeded = seedSource("c-blob.txt", workspaceA, "user_a");
    // No index generation is seeded here, so the owner gets an empty chunk page; the foreign checks are the point.
    expect((await listChunks(workspaceA, "user_a", seeded.fid))?.items).toEqual([]);
    expect(await resolveVersionBlob(workspaceA, "user_a", seeded.fid, null)).toEqual({
      url: blobUrl, mimeType: "text/plain", fileName: "c-blob.txt", versionNumber: 1, blobVersionNumber: 1,
    });
    expect(h.contextAfter).toBe("");
    for (const [wid, uid] of foreignPairs) {
      expect(await listChunks(wid, uid, seeded.fid)).toBeNull();
      expect(await resolveVersionBlob(wid, uid, seeded.fid, null)).toBeNull();
    }
    expect(h.contextAfter).toBe("");
  }, 60_000);

  it("hardDeleteSource deletes only the caller's own Source; a foreign workspace gets 'missing' and no blob is touched", async () => {
    const seeded = seedSource("c-delete.txt", workspaceA, "user_a");
    h.deleteBlob.mockClear();
    expect(await hardDeleteSource({ workspaceId: workspaceB, userId: "user_b", sourceId: seeded.fid })).toBe("missing");
    expect(await hardDeleteSource({ workspaceId: workspaceA, userId: "user_b", sourceId: seeded.fid })).toBe("missing");
    expect(h.deleteBlob).not.toHaveBeenCalled();
    expect(run("select count(*) from files where id = $1", [seeded.fid], true)[0][0]).toBe(1);
    // A blob failure takes the backoff UPDATE (scoped to the owner's workspace) and keeps the row.
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    h.deleteBlob.mockRejectedValueOnce(new Error("blob down"));
    expect(await hardDeleteSource({ workspaceId: workspaceA, userId: "user_a", sourceId: seeded.fid })).toBe("blob_failed");
    consoleError.mockRestore();
    expect(run("select purge_attempts from files where id = $1", [seeded.fid], true)[0][0]).toBe(1);
    expect(h.contextAfter).toBe("");
    expect(await hardDeleteSource({ workspaceId: workspaceA, userId: "user_a", sourceId: seeded.fid })).toBe("deleted");
    expect(h.deleteBlob).toHaveBeenLastCalledWith(blobUrl);
    expect(run("select count(*) from files where id = $1", [seeded.fid], true)[0][0]).toBe(0);
    expect(h.contextAfter).toBe("");
  }, 60_000);
});
