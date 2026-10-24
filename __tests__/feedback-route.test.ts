import { beforeEach, describe, expect, it, vi } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";

const h = vi.hoisted(() => ({
  batches: [] as unknown[][],
  results: [] as unknown[][],
  wheres: [] as unknown[],
  inserted: [] as Array<Record<string, unknown>>,
}));

vi.mock("@/lib/db-config", () => {
  const chain: unknown = new Proxy(function () {}, {
    get: (_target, property) => {
      if (property === "then") return undefined;
      if (property === "where") return (value: unknown) => { h.wheres.push(value); return chain; };
      if (property === "values") return (value: Record<string, unknown>) => { h.inserted.push(value); return chain; };
      return () => chain;
    },
  });
  return {
    db: {
      select: () => chain,
      insert: () => chain,
      execute: () => chain,
      batch: async (items: unknown[]) => {
        h.batches.push(items);
        return h.results.shift() ?? [[], ...items.slice(1).map(() => [])];
      },
    },
  };
});
vi.mock("@/lib/workspace", () => ({
  resolveWorkspace: async () => ({ userId: "user_a", workspaceId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa" }),
}));

import { POST } from "@/app/api/feedback/route";

const WS = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RAG_ID = "legacy-trace-1";
const RETRIEVAL_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const messageRows = [{
  id: "answer-1",
  chatId: "chat-1",
  role: "assistant",
  parts: [
    { type: "tool-invocation", toolName: "searchKnowledgeBase", result: { traceId: "legacy-trace-0" } },
    { type: "tool-invocation", toolName: "searchKnowledgeBase", result: { traceId: RAG_ID } },
    { type: "tool-searchKnowledgeBase", state: "output-available", output: { engine: "python", traceId: RETRIEVAL_ID } },
  ],
}];

const req = (body: unknown) => new Request("https://scribe.example/api/feedback", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

beforeEach(() => {
  h.batches.length = 0;
  h.results.length = 0;
  h.wheres.length = 0;
  h.inserted.length = 0;
});

describe("POST /api/feedback", () => {
  it("rejects malformed and client-supplied trace/workspace data before database access", async () => {
    for (const body of [null, {}, { messageId: "m", rating: "neutral" }, { messageId: "m", rating: "up", retrievalTraceId: RETRIEVAL_ID }]) {
      expect((await POST(req(body))).status).toBe(400);
    }
    expect(h.batches).toHaveLength(0);
  });

  it("returns the same not-found response for answers outside the caller's scoped chats", async () => {
    h.results.push([[], []]);
    const response = await POST(req({ messageId: "foreign-answer", rating: "down" }));
    expect(response.status).toBe(404);
    const queries = h.wheres.map((where) => new PgDialect().sqlToQuery(where as never));
    expect(queries.flatMap((query) => query.params)).toContain("foreign-answer");
    expect(queries.flatMap((query) => query.params)).toContain("user_a");
    expect(queries.flatMap((query) => query.params)).toContain(WS);
    expect(h.batches).toHaveLength(1);
    expect(h.inserted).toHaveLength(0);
  });

  it("links latest traces from the persisted assistant parts and inserts only session-owned feedback", async () => {
    h.results.push(
      [[], messageRows],
      [[], [{ id: RAG_ID }], [{ id: RETRIEVAL_ID }]],
      [[], [{ id: 17 }]],
    );
    const response = await POST(req({
      messageId: "answer-1",
      rating: "down",
      reasons: ["wrong_citation", "missing_info"],
      comment: "  Citation points to the wrong section.  ",
    }));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ ok: true });
    expect(h.batches).toHaveLength(3);
    expect(h.inserted).toEqual([{
      workspaceId: WS,
      chatId: "chat-1",
      messageId: "answer-1",
      ragTraceId: RAG_ID,
      retrievalTraceId: RETRIEVAL_ID,
      userId: "user_a",
      rating: "down",
      reasons: ["wrong_citation", "missing_info"],
      comment: "Citation points to the wrong section.",
    }]);
  });

  it("stores feedback when a linked trace was deleted or belongs to another workspace as null", async () => {
    h.results.push([[], messageRows], [[], [], []], [[], [{ id: 18 }]]);
    const response = await POST(req({ messageId: "answer-1", rating: "up" }));
    expect(response.status).toBe(200);
    expect(h.inserted[0]).toMatchObject({ ragTraceId: null, retrievalTraceId: null, reasons: [], comment: null });
  });
});
