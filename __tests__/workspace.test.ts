import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * These tests drive the real Drizzle query builder through a fake pg-proxy driver,
 * so we assert on the SQL and bound parameters that would be sent to Postgres.
 * Workspaces are held in an in-memory store that mimics the partial unique index
 * (one personal workspace per owner) and ON CONFLICT DO NOTHING.
 */
const h = vi.hoisted(() => ({
  queries: [] as Array<{ sql: string; params: unknown[] }>,
  workspaces: new Map<string, string>(), // owner -> workspace id
  members: new Set<string>(),
  insertAttempts: 0,
  currentUser: "user_a",
}));

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

      if (/^insert into "workspaces"/i.test(query)) {
        h.insertAttempts += 1;
        // Postgres serialises the unique-index check: first insert wins.
        if (owner && !h.workspaces.has(owner)) {
          h.workspaces.set(owner, `ws-${owner}`);
        }
        return { rows: [] };
      }
      if (/^insert into "workspace_members"/i.test(query)) {
        const wsId = params[0] as string;
        h.members.add(`${wsId}:${owner}`);
        return { rows: [] };
      }
      if (/^select/i.test(query) && /from "workspaces"/i.test(query)) {
        const id = owner ? h.workspaces.get(owner) : undefined;
        if (!id) return { rows: [] };
        // Fast path joins workspace_members; only return when membership exists.
        if (/"workspace_members"/.test(query) && !h.members.has(`${id}:${owner}`)) {
          return { rows: [] };
        }
        return { rows: [[id]] };
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
    (q) => !/"workspaces"|"workspace_members"/.test(q.sql)
  );
}

beforeEach(() => {
  h.queries.length = 0;
  h.workspaces.clear();
  h.members.clear();
  h.insertAttempts = 0;
  h.currentUser = "user_a";
});

describe("ensurePersonalWorkspace", () => {
  it("creates exactly one workspace under concurrent first requests", async () => {
    const ids = await Promise.all(
      Array.from({ length: 12 }, () => ensurePersonalWorkspace("user_a"))
    );

    expect(new Set(ids).size).toBe(1);
    expect(h.workspaces.size).toBe(1);
    expect(h.insertAttempts).toBeGreaterThan(1); // racers really did all try to insert
    // Insert uses ON CONFLICT DO NOTHING so losing racers are no-ops.
    const insert = h.queries.find((q) => /^insert into "workspaces"/i.test(q.sql));
    expect(insert?.sql).toMatch(/on conflict do nothing/i);
  });

  it("is idempotent and takes the read-only fast path once the workspace exists", async () => {
    const first = await ensurePersonalWorkspace("user_a");
    h.queries.length = 0;
    const second = await ensurePersonalWorkspace("user_a");

    expect(second).toBe(first);
    expect(h.queries.every((q) => /^select/i.test(q.sql))).toBe(true);
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
    const select = qs.find((q) => /^select/i.test(q.sql))!;
    expect(select.sql).toContain('"workspace_id" = $');
    expect(select.params).toContain("ws-user_a");
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
