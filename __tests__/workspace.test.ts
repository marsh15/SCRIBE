import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * These tests drive the real Drizzle query builder through a fake pg-proxy driver,
 * so we assert on the SQL and bound parameters that would be sent to Postgres.
 * The fake function models one personal workspace per user; the real database
 * function and its concurrency guarantees are covered by integration tests.
 */
const h = vi.hoisted(() => ({
  queries: [] as Array<{ sql: string; params: unknown[] }>,
  workspaces: new Map<string, string>(), // owner -> workspace id
  members: new Set<string>(),
  failResolution: false,
  databaseError: false,
  currentUser: "user_a",
}));

vi.mock("@/lib/scoped", () => ({ scoped: () => ({ batch: async (queries: PromiseLike<unknown>[]) => Promise.all(queries) }) }));

vi.mock("@/lib/auth", () => ({
  getUserId: async () => h.currentUser,
  isNotAuthenticatedError: () => false,
}));

vi.mock("@/lib/embeddings", () => ({
  generateEmbedding: async () => [0.1, 0.2, 0.3],
}));

vi.mock("@/lib/ingestion/source-intake", () => ({ deleteSource: vi.fn() }));

vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));

vi.mock("@/lib/db-config", async () => {
  const { drizzle } = await import("drizzle-orm/pg-proxy");
  const schema = await import("@/lib/db-schema");
  const jitter = () => new Promise((r) => setTimeout(r, Math.random() * 4));

  const db = drizzle(
    async (query, params) => {
      h.queries.push({ sql: query, params });
      await jitter();
      const owner = params.find(
        (p): p is string => typeof p === "string" && p.startsWith("user_")
      );

      if (/app_ensure_personal_workspace/i.test(query)) {
        if (h.databaseError) throw new Error("database unavailable");
        if (h.failResolution) return { rows: [] };
        if (owner && !h.workspaces.has(owner)) h.workspaces.set(owner, `ws-${owner}`);
        const id = owner ? h.workspaces.get(owner) : undefined;
        if (id) h.members.add(`${id}:${owner}`);
        return { rows: id ? [[id]] : [] };
      }
      return { rows: [] };
    },
    { schema }
  );
  return { db, sql: vi.fn() };
});

import { ensurePersonalWorkspace, resolveWorkspace } from "@/lib/workspace";
import { getChats, getChatMessages, createChat, deleteChat } from "@/app/chat/actions";
import { getFiles } from "@/app/files/actions";
import { searchDocuments } from "@/lib/search";
import { getLatestRagTraceView, recordRagTrace } from "@/lib/rag-observability";

function nonWorkspaceQueries() {
  return h.queries.filter(
    (q) => !/app_ensure_personal_workspace/.test(q.sql)
  );
}

beforeEach(() => {
  h.queries.length = 0;
  h.workspaces.clear();
  h.members.clear();
  h.failResolution = false;
  h.databaseError = false;
  h.currentUser = "user_a";
});

describe("ensurePersonalWorkspace", () => {
  it("creates exactly one workspace under concurrent first requests", async () => {
    const ids = await Promise.all(
      Array.from({ length: 12 }, () => ensurePersonalWorkspace("user_a"))
    );

    expect(new Set(ids).size).toBe(1);
    expect(h.workspaces.size).toBe(1);
    expect(h.members.size).toBe(1);
    expect(h.queries.every((q) => /app_ensure_personal_workspace\(\$1\)/i.test(q.sql))).toBe(true);
    expect(h.queries.every((q) => q.params[0] === "user_a")).toBe(true);
  });

  it("is idempotent when the workspace exists", async () => {
    const first = await ensurePersonalWorkspace("user_a");
    h.queries.length = 0;
    const second = await ensurePersonalWorkspace("user_a");

    expect(second).toBe(first);
    expect(h.workspaces.size).toBe(1);
  });

  it("fails closed for missing user or missing database result", async () => {
    await expect(ensurePersonalWorkspace("")).rejects.toThrow("requires a userId");
    expect(h.queries).toHaveLength(0);
    h.failResolution = true;
    await expect(resolveWorkspace()).rejects.toThrow("Failed to resolve personal workspace");
  });

  it("binds the user ID and propagates database errors", async () => {
    const user = "user_a' ; select 1; --";
    await ensurePersonalWorkspace(user);
    expect(h.queries[0].params).toEqual([user]);
    expect(h.queries[0].sql).not.toContain(user);
    h.databaseError = true;
    await expect(ensurePersonalWorkspace(user)).rejects.toThrow("Failed query");
  });

  it("gives different users different workspaces", async () => {
    const [a, b] = await Promise.all([
      ensurePersonalWorkspace("user_a"),
      ensurePersonalWorkspace("user_b"),
    ]);
    expect(a).not.toBe(b);
  });

  it("resolveWorkspace derives the workspace from the authenticated user", async () => {
    h.currentUser = "user_b";
    await expect(resolveWorkspace()).resolves.toEqual({
      userId: "user_b",
      workspaceId: "ws-user_b",
    });
  });
});

describe("workspace scoping of queries", () => {
  it("getFiles filters on the caller's workspace id only", async () => {
    h.currentUser = "user_a";
    await getFiles();
    const [q] = nonWorkspaceQueries();
    expect(q.sql).toContain('"workspace_id" = $');
    expect(q.params).toContain("ws-user_a");
    expect(q.params).not.toContain("ws-user_b");
  });

  it("getChats and getChatMessages filter on workspace_id", async () => {
    await getChats();
    await getChatMessages("chat-1");
    const qs = nonWorkspaceQueries();
    expect(qs).toHaveLength(2);
    for (const q of qs) {
      expect(q.sql).toContain('"chats"."workspace_id" = $');
      expect(q.params).toContain("ws-user_a");
    }
  });

  it("createChat and deleteChat set / filter workspace_id", async () => {
    await createChat("hello");
    await deleteChat("chat-1");
    const qs = nonWorkspaceQueries();
    const insert = qs.find((q) => /^insert into "chats"/i.test(q.sql))!;
    expect(insert.sql).toContain('"workspace_id"');
    expect(insert.params).toContain("ws-user_a");
    const deletion = qs.find((q) => /^delete from "chats"/i.test(q.sql))!;
    expect(deletion.sql).toContain('"workspace_id" = $');
    expect(deletion.params).toContain("ws-user_a");
  });

  it("user B never gets user A's workspace id in bound params", async () => {
    h.currentUser = "user_a";
    await ensurePersonalWorkspace("user_a");
    h.currentUser = "user_b";
    h.queries.length = 0;
    await getFiles();
    await getChats();
    for (const q of nonWorkspaceQueries()) {
      expect(q.params).toContain("ws-user_b");
      expect(q.params).not.toContain("ws-user_a");
    }
  });

  it("searchDocuments joins files and filters retrieval by workspace_id", async () => {
    await searchDocuments("q", "user_a", "ws-user_a", 5, 0.3);
    const q = h.queries.find((x) => /from "documents"/i.test(x.sql))!;
    expect(q.sql).toMatch(/"files"\."workspace_id" = \$/);
    expect(q.params).toContain("ws-user_a");
  });

  it("trace reads and writes carry workspace_id", async () => {
    await getLatestRagTraceView({
      userId: "user_a",
      workspaceId: "ws-user_a",
      chatId: "chat-1",
    });
    const read = h.queries.find((x) => /from "rag_traces"/i.test(x.sql))!;
    expect(read.sql).toMatch(/"workspace_id" = \$/);
    expect(read.params).toContain("ws-user_a");

    await recordRagTrace({
      chatId: "chat-1",
      userId: "user_a",
      workspaceId: "ws-user_a",
      search: {
        query: "q",
        documents: [],
        timings: { embeddingMs: 1, retrievalMs: 1, totalMs: 2 },
        topK: 5,
        threshold: 0.3,
      },
    });
    const insert = h.queries.find((x) => /^insert into "rag_traces"/i.test(x.sql))!;
    expect(insert.sql).toContain('"workspace_id"');
    expect(insert.params).toContain("ws-user_a");
  });
});
