import { execFileSync } from "node:child_process";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

// A dedicated local test database and a real non-owner LOGIN role; never use a deployment URL.
const appUrl = process.env.CHAT_RLS_DATABASE_URL;
const adminUrl = process.env.CHAT_RLS_ADMIN_URL;
const localTestUrl = (url: string) => ["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname) && new URL(url).pathname.endsWith("_test");
const enabled = !!appUrl && !!adminUrl && localTestUrl(appUrl) && localTestUrl(adminUrl) && new URL(appUrl).pathname === new URL(adminUrl).pathname;
const workspaceA = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const workspaceB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const h = vi.hoisted(() => ({ user: "user_a", workspace: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", contextAfter: "", run: (_queries: { sql: string; params: unknown[] }[], _admin = false): unknown[][][] => [], queued: null as null | Array<{ sql: string; params: unknown[]; resolve: (result: { rows: unknown[] }) => void; reject: (err: Error) => void }> }));
vi.mock("@/lib/auth", () => ({ getUserId: async () => h.user }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: async () => ({ userId: h.user, workspaceId: h.workspace }) }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/embeddings", () => ({ generateEmbedding: async () => Array(3072).fill(0.1) }));
vi.mock("@/lib/db-config", async () => {
  const { drizzle } = await import("drizzle-orm/pg-proxy");
  const schema = await import("@/lib/db-schema");
  const db = drizzle(async (sql, params) => {
    if (!h.queued) return { rows: h.run([{ sql, params }])[0] };
    return new Promise((resolve, reject) => h.queued!.push({ sql, params, resolve, reject }));
  }, { schema });
  Object.assign(db, { batch: async (queries: PromiseLike<unknown>[]) => {
    const pending: NonNullable<typeof h.queued> = [];
    h.queued = pending;
    const promises = queries.map((q) => Promise.resolve(q));
    await new Promise((resolve) => setImmediate(resolve));
    h.queued = null;
    try {
      const results = h.run(pending);
      pending.forEach((q, i) => q.resolve({ rows: results[i] }));
    } catch (error) {
      pending.forEach((q) => q.reject(error as Error));
    }
    return Promise.all(promises);
  }});
  return { db };
});
import { getChats, getChatMessages, createChat, deleteChat } from "@/app/chat/actions";
import { scoped } from "@/lib/scoped";
import { completeRagTrace, getLatestRagTraceView, recordRagEvaluation, recordRagTrace } from "@/lib/rag-observability";
import { db } from "@/lib/db-config";
import { sql } from "drizzle-orm";
import { searchDocuments } from "@/lib/search";

h.run = (queries, admin = false) => {
  const input = JSON.stringify({ dsn: admin ? adminUrl : appUrl, queries });
  const output = execFileSync("intelligence/.venv/bin/python", ["__tests__/support/pg_parameterized.py"], { input, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] });
  const result = JSON.parse(output);
  h.contextAfter = result.context_after;
  if (result.error) throw new Error(result.error);
  return result.results;
};
const run = (statement: string, params: unknown[] = [], admin = false) => h.run([{ sql: statement, params }], admin)[0];

describe.skipIf(!enabled)("chat lifecycle under RLS with a non-owner login", () => {
  beforeAll(() => {
    const role = run("select current_user, r.rolsuper, r.rolbypassrls, (select tableowner = current_user from pg_tables where schemaname='public' and tablename='chats') from pg_roles r where r.rolname=current_user")[0];
    expect(role).toEqual([new URL(appUrl!).username, false, false, false]);
    run("insert into workspaces(id,kind,owner_user_id) values ($1,'personal','user_a'),($2,'personal','user_b')", [workspaceA, workspaceB], true);
    run("insert into chats(id,title,user_id,workspace_id) values ('foreign-chat','Private','user_b',$1)", [workspaceB], true);
    run("insert into chat_messages(id,chat_id,role,content) values ('foreign-message','foreign-chat','assistant','secret')", [], true);
  });
  afterAll(() => { run("delete from chats where workspace_id in ($1,$2)", [workspaceA, workspaceB], true); run("delete from files where workspace_id in ($1,$2)", [workspaceA, workspaceB], true); run("delete from workspaces where id in ($1,$2)", [workspaceA, workspaceB], true); });
  it("isolates reads and writes and runs the exported chat actions", async () => {
    expect(run("select id from chats")).toEqual([]);
    expect(() => run("insert into chats(id,title,user_id,workspace_id) values ('unscoped','x','user_a',$1)", [workspaceA])).toThrow();
    expect(await getChats()).toEqual([]);
    expect(await getChatMessages("foreign-chat")).toEqual([]);
    expect(await deleteChat("foreign-chat")).toBe(false);
    const chat = await createChat("Mine");
    expect(h.contextAfter).toBe("");
    expect(chat.title).toBe("Mine");
    expect((await getChats()).map((c) => c.id)).toEqual([chat.id]);
    h.user = "user_b"; h.workspace = workspaceB;
    expect((await getChats()).map((c) => c.id)).toEqual(["foreign-chat"]);
    expect(await deleteChat(chat.id)).toBe(false);
    h.user = "user_a"; h.workspace = workspaceA;
    expect(await deleteChat(chat.id)).toBe(true);
  }, 60_000);
  it("persists messages and trace lifecycle only in the owning workspace", async () => {
    h.user = "user_a"; h.workspace = workspaceA;
    const chat = await createChat("Trace test");
    const file = run("insert into files(name,type,size,user_id,workspace_id,status) values ('trace.txt','text/plain',1,'user_a',$1,'ready') returning id", [workspaceA], true)[0][0];
    await scoped(workspaceA).batch([db.execute(sql`insert into chat_messages(id,chat_id,role,content) values ('trace-message',${chat.id},'assistant','answer')`)]);
    const result = await recordRagTrace({ chatId: chat.id, userId: h.user, workspaceId: workspaceA, search: {
      query: "q", documents: [{ id: 1, content: "Stored passage", metadata: { chunkIndex: 0 }, similarity: 0.8,
        file: { id: Number(file), name: "trace.txt", type: "text/plain" } }],
      timings: { embeddingMs: 1, retrievalMs: 2, totalMs: 3 }, topK: 5, threshold: 0.3,
    } });
    await completeRagTrace({ traceId: result.traceId!, workspaceId: workspaceA, assistantMessageId: "trace-message", generationMs: 4, totalMs: 7 });
    await recordRagEvaluation({ traceId: result.traceId!, status: "completed", judgeModel: "test" }, workspaceA);
    const trace = await getLatestRagTraceView({ userId: h.user, workspaceId: workspaceA, chatId: chat.id });
    expect(trace?.trace.assistantMessageId).toBe("trace-message");
    expect(trace?.evaluation?.status).toBe("completed");
    expect(trace?.chunks).toHaveLength(1);
    expect(trace?.chunks[0].contentPreview).toBe("Stored passage");
    expect((await getChatMessages(chat.id)).map((message) => message.id)).toEqual(["trace-message"]);
    h.user = "user_b"; h.workspace = workspaceB;
    expect(await getLatestRagTraceView({ userId: h.user, workspaceId: workspaceB, chatId: chat.id })).toBeNull();
    expect(await getChatMessages(chat.id)).toEqual([]);
    await expect(recordRagEvaluation({ traceId: result.traceId!, status: "completed", judgeModel: "test" }, workspaceB)).rejects.toThrow();
    await completeRagTrace({ traceId: result.traceId!, workspaceId: workspaceB, assistantMessageId: "trace-message", generationMs: 99, totalMs: 99 });
    h.user = "user_a"; h.workspace = workspaceA;
    expect((await getLatestRagTraceView({ userId: h.user, workspaceId: workspaceA, chatId: chat.id }))?.trace.generationMs).toBe(4);
    expect(await deleteChat(chat.id)).toBe(true);
  }, 60_000);

  it("rolls back failed batches and does not leak context", async () => {
    await expect(scoped(workspaceA).batch([
      db.execute(sql`insert into chats(id,title,user_id,workspace_id) values ('rollback-chat','x','user_a',${workspaceA})`),
      db.execute(sql`insert into chats(id,title,user_id,workspace_id) values ('wrong-workspace','x','user_b',${workspaceB})`),
    ])).rejects.toThrow();
    expect(h.contextAfter).toBe("");
    expect(run("select id from chats where id = $1", ["rollback-chat"], true)).toEqual([]);
    expect(run("select current_setting('app.workspace_id', true)")[0][0]).toBeNull();
  }, 60_000);

  it("retrieves legacy documents only from the authenticated workspace", async () => {
    for (const [workspace, user] of [[workspaceA, "user_a"], [workspaceB, "user_b"]]) {
      run("with f as (insert into files(name,type,size,user_id,workspace_id,status) values ('policy.txt','text/plain',1,$1,$2,'ready') returning id) insert into documents(file_id,chunk_index,content,embeddings) select id,0,$1,array_fill(0.1::real,array[3072])::vector from f", [user, workspace], true);
    }
    const a = await searchDocuments("policy", "user_a", workspaceA);
    expect(a.documents.map((document) => document.content)).toEqual(["user_a"]);
    expect(h.contextAfter).toBe("");
    const b = await searchDocuments("policy", "user_b", workspaceB);
    expect(b.documents.map((document) => document.content)).toEqual(["user_b"]);
    expect(run("select content from documents")).toEqual([]);
  }, 60_000);
});
