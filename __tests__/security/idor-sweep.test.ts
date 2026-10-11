/**
 * T27 access-control sweep. Table-driven over __tests__/security/route-table.ts: every route and server action that takes
 * an id is called as workspace A / user A with an id that belongs to workspace B (or to nobody). Each must answer exactly
 * as it does for an unknown id (404, or the documented empty result), must never execute a mutation, and must have put
 * the SESSION workspace and user (never a request value) into its query. Internal routes answer 401 without CRON_SECRET.
 * route-guard.test.ts fails when a route or action exists that this table does not know.
 */
import { PgDialect } from "drizzle-orm/pg-core";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ACTION_TABLE, ROUTE_TABLE } from "./route-table";

const WS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const USER_A = "user_A";
const FOREIGN_SOURCE = 4242;
const FOREIGN_CHAT = "chat-of-workspace-B";
const FOREIGN_MESSAGE = "msg-of-workspace-B";
const FOREIGN_EVIDENCE = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

const S = vi.hoisted(() => {
  const wheres: unknown[] = [];
  /** A chainable stand-in for the Drizzle query builder: records `where` clauses, resolves to `rows`. */
  const chain = (rows: unknown[]): unknown => {
    const c: unknown = new Proxy(function () {}, {
      get: (_t, prop) => {
        if (prop === "then") return (resolve: (v: unknown) => unknown) => resolve(rows);
        return (...args: unknown[]) => {
          if (prop === "where") wheres.push(args[0]);
          return c;
        };
      },
      apply: () => c,
    });
    return c;
  };
  const findFirst = async (arg?: { where?: unknown }) => {
    if (arg?.where) wheres.push(arg.where);
    return undefined;
  };
  return {
    clerkAuth: vi.fn(),
    sql: vi.fn(),
    wheres,
    chain,
    findFirst,
    mutations: [] as string[],
    callIntelligence: vi.fn(),
    after: vi.fn(),
    fireTick: vi.fn(),
    runSweep: vi.fn(),
    runPurge: vi.fn(),
    runStep: vi.fn(),
    runQueuedSourceIntake: vi.fn(),
    deleteSource: vi.fn(),
    reserveSourceUpload: vi.fn(),
    markPaymentEventIfNew: vi.fn(),
    setUserSubscription: vi.fn(),
    completeSourceUpload: vi.fn(),
    downloadBlob: vi.fn(),
    env: { INGEST_ENGINE: "python", RETRIEVAL_ENGINE: "python", CRON_SECRET: "cron-secret" } as Record<string, string | undefined>,
  };
});

vi.mock("@clerk/nextjs/server", () => ({ auth: S.clerkAuth, currentUser: vi.fn(async () => null) }));
vi.mock("next/server", async () => ({ ...(await vi.importActual<typeof import("next/server")>("next/server")), after: S.after }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("@/lib/env", () => ({ env: S.env, requireEnv: () => "x" }));
vi.mock("@/lib/workspace", () => ({
  ensurePersonalWorkspace: async () => WS_A,
  resolveWorkspace: async () => {
    const { getUserId } = await import("@/lib/auth");
    return { userId: await getUserId(), workspaceId: WS_A };
  },
}));
vi.mock("@/lib/intelligence-client", () => ({ callIntelligence: S.callIntelligence, baseUrl: () => "https://scribe.example" }));

vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => S.sql(strings.join("?"), values),
  db: {
    select: () => S.chain([]),
    insert: () => ({ values: async () => void S.mutations.push("insert") }),
    update: () => ({ set: () => ({ where: async () => void S.mutations.push("update") }) }),
    delete: () => ({ where: async () => void S.mutations.push("delete") }),
    query: {
      chats: { findFirst: S.findFirst },
      files: { findFirst: S.findFirst },
      ragTraces: { findFirst: S.findFirst },
      ragTraceChunks: { findMany: async () => [] },
      ragEvaluations: { findFirst: S.findFirst },
    },
    execute: vi.fn(async () => []),
  },
}));
vi.mock("@/lib/storage/blob", () => ({ downloadBlobToBuffer: S.downloadBlob, getBlobMetadata: vi.fn(), deleteBlob: vi.fn() }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: S.fireTick, runSweep: S.runSweep, runPurge: S.runPurge, runStep: S.runStep, MAX_TICK_HOPS: 200 }));
vi.mock("@/lib/ingestion/source-intake", async () => {
  const policy = await vi.importActual<typeof import("@/lib/ingestion/source-intake-policy")>("@/lib/ingestion/source-intake-policy");
  return {
    SourceIntakeError: policy.SourceIntakeError,
    parseSourceUploadCompletionPayload: (p: string) => JSON.parse(p),
    completeSourceUpload: S.completeSourceUpload,
    runQueuedSourceIntake: S.runQueuedSourceIntake,
    deleteSource: S.deleteSource,
    reserveSourceUpload: S.reserveSourceUpload,
  };
});
vi.mock("@/lib/billing/usage", () => ({
  getUsageSummary: vi.fn(async () => ({ allowOverage: true, projectedOverageInr: 0, planCode: "free" })),
  recordUsageEvent: vi.fn(),
}));
vi.mock("@/lib/uploads/limits", () => ({ getUserMaxUploadBytes: vi.fn(async () => ({ maxBytes: 1e9, maxMb: 1000, planCode: "free" })) }));
vi.mock("@vercel/blob/client", async () => ({
  ...(await vi.importActual<typeof import("@vercel/blob/client")>("@vercel/blob/client")),
  generateClientTokenFromReadWriteToken: vi.fn(async () => "client-token"),
}));
vi.mock("@/lib/flags", () => ({ flags: { billingEnabled: true } }));
vi.mock("@/lib/billing/gateways/razorpay", () => ({ createRazorpayOrder: vi.fn(), createRazorpayPortalLink: vi.fn() }));
vi.mock("@/lib/billing/portal", () => ({ isBillingPortalAvailable: () => true }));
vi.mock("@/lib/billing/store", () => ({
  markPaymentEventIfNew: S.markPaymentEventIfNew,
  markPaymentEventProcessed: vi.fn(),
  setUserSubscription: S.setUserSubscription,
}));
vi.mock("@/lib/search", () => ({ searchDocuments: vi.fn() }));
vi.mock("@/lib/rag-eval", () => ({ evaluateRagAnswer: vi.fn() }));
vi.mock("@/lib/db-compat", () => ({ ensureDocumentsChunkIndexSchema: vi.fn() }));

import { POST as chatPOST } from "@/app/api/chat/route";
import { GET as evidenceGET } from "@/app/api/evidence/[id]/route";
import { GET as viewGET } from "@/app/api/files/[id]/view/route";
import { GET as healthGET } from "@/app/api/health/route";
import { GET as runGET } from "@/app/api/internal/ingest/run/route";
import { GET as sweepGET } from "@/app/api/internal/ingest/sweep/route";
import { GET as tickGET } from "@/app/api/internal/ingest/tick/route";
import { GET as ragLatestGET } from "@/app/api/rag/latest/route";
import { POST as processNowPOST } from "@/app/api/sources/process-now/route";
import { POST as reservePOST } from "@/app/api/sources/reserve/route";
import { POST as uploadCompletePOST } from "@/app/api/sources/upload-complete/route";
import { POST as versionsPOST } from "@/app/api/sources/[id]/versions/route";
import { POST as razorpayPOST } from "@/app/api/webhooks/razorpay/route";
import { POST as billingCheckoutPOST } from "@/app/api/billing/checkout/route";
import { POST as billingPortalPOST } from "@/app/api/billing/portal/route";
import { GET as billingUsageGET } from "@/app/api/billing/usage/route";
import { POST as billingVerifyPOST } from "@/app/api/billing/verify/route";
import * as batchRoot from "@/app/api/ingest/batch/route";
import * as batchInit from "@/app/api/ingest/batch/init/route";
import * as batchFinalize from "@/app/api/ingest/batch/finalize/route";
import * as uploadsComplete from "@/app/api/uploads/complete/route";
import * as uploadsSign from "@/app/api/uploads/sign/route";

import * as chatActions from "@/app/chat/actions";
import * as filesActions from "@/app/files/actions";
import * as detailActions from "@/app/files/[id]/actions";

const dialect = new PgDialect();
const rendered = () => S.wheres.map((w) => dialect.sqlToQuery(w as never));
const signedIn = () => S.clerkAuth.mockResolvedValue({ userId: USER_A });
const signedOut = () => S.clerkAuth.mockResolvedValue({ userId: null });
const url = (p: string, init?: RequestInit) => new Request(`https://scribe.example${p}`, init);
const ctx = (id: string) => ({ params: Promise.resolve({ id }) });

beforeEach(() => {
  vi.clearAllMocks();
  vi.unstubAllEnvs();
  S.wheres.length = 0;
  S.mutations.length = 0;
  S.env.INGEST_ENGINE = "python";
  S.env.CRON_SECRET = "cron-secret";
  S.sql.mockResolvedValue([]);
  S.callIntelligence.mockResolvedValue(new Response("{}", { status: 404 }));
  if (!process.env.SHOW_ERR) vi.spyOn(console, "error").mockImplementation(() => undefined);
  vi.spyOn(console, "info").mockImplementation(() => undefined);
  signedIn();
});

/** Assert the query text/params carry the session workspace and user (and the id), and that a soft-deleted row is excluded. */
function expectScoped(calls: Array<{ text: string; values: unknown[] }>, id: unknown, opts: { notDeleted?: boolean } = {}) {
  expect(calls.length).toBeGreaterThan(0);
  const first = calls[0];
  expect(first.values).toContain(WS_A);
  expect(first.values).toContain(USER_A);
  expect(first.values).toContain(id);
  expect(first.text).toMatch(/"workspace_id" = \?/);
  expect(first.text).toMatch(/"user_id" = \?/);
  if (opts.notDeleted !== false) expect(first.text).toContain('"deleted_at" IS NULL');
}
const sqlCalls = () => S.sql.mock.calls.map(([text, values]) => ({ text: text as string, values: values as unknown[] }));
function expectWhereHas(...needles: unknown[]) {
  const q = rendered();
  expect(q.length).toBeGreaterThan(0);
  for (const n of needles) expect(q.some((r) => r.params.includes(n)), `where lacks ${String(n)}`).toBe(true);
}
async function drain() {
  for (const [cb] of S.after.mock.calls) await (cb as () => Promise<void>)();
}

// ---------------------------------------------------------------------------------------------- route handlers

const ROUTE_HANDLERS: Record<string, () => Promise<void>> = {
  "app/api/chat/route.ts": async () => {
    vi.stubEnv("GOOGLE_GENERATIVE_AI_API_KEY", "test-key");
    const post = (chatId: string) =>
      chatPOST(url(`/api/chat?chatId=${chatId}`, { method: "POST", body: JSON.stringify({ messages: [{ id: "m1", role: "user", parts: [{ type: "text", text: "hi" }] }] }) }));
    signedOut();
    expect((await post(FOREIGN_CHAT)).status).toBe(401);
    signedIn();
    const res = await post(FOREIGN_CHAT);
    expect(res.status).toBe(404);
    expect(await res.text()).toBe("Chat not found.");
    expectWhereHas(FOREIGN_CHAT, USER_A, WS_A);
    expect(S.mutations).toEqual([]); // the user message of a foreign chat is not saved
  },

  "app/api/evidence/[id]/route.ts": async () => {
    signedOut();
    expect((await evidenceGET(url(`/api/evidence/${FOREIGN_EVIDENCE}`), ctx(FOREIGN_EVIDENCE))).status).toBe(401);
    signedIn();
    const foreign = await evidenceGET(url(`/api/evidence/${FOREIGN_EVIDENCE}?workspace_id=ws-B`, { headers: { "x-workspace-id": "ws-B" } }), ctx(FOREIGN_EVIDENCE));
    const malformed = await evidenceGET(url("/api/evidence/not-a-uuid"), ctx("not-a-uuid"));
    expect(foreign.status).toBe(404);
    expect(malformed.status).toBe(404);
    expect(await foreign.json()).toEqual(await malformed.json()); // identical bodies: no existence oracle
    const [path, token, init] = S.callIntelligence.mock.calls[0];
    expect(path).toBe("/evidence/open");
    expect(token).toEqual({ sub: USER_A, wid: WS_A, scope: "evidence_read" }); // never the query/header value
    expect(JSON.parse(init.body)).toEqual({ item_id: FOREIGN_EVIDENCE });
  },

  "app/api/files/[id]/view/route.ts": async () => {
    signedOut();
    expect((await viewGET(url(`/api/files/${FOREIGN_SOURCE}/view`), ctx(String(FOREIGN_SOURCE)))).status).toBe(401);
    signedIn();
    // legacy path (no ?version): Drizzle where with id + user + workspace
    let res = await viewGET(url(`/api/files/${FOREIGN_SOURCE}/view`), ctx(String(FOREIGN_SOURCE)));
    expect(res.status).toBe(404);
    expectWhereHas(FOREIGN_SOURCE, USER_A, WS_A);
    // version path: raw SQL scoped by workspace + user + not deleted
    S.sql.mockClear();
    res = await viewGET(url(`/api/files/${FOREIGN_SOURCE}/view?version=2`), ctx(String(FOREIGN_SOURCE)));
    expect(res.status).toBe(404);
    expectScoped(sqlCalls(), FOREIGN_SOURCE);
    expect(S.downloadBlob).not.toHaveBeenCalled();
  },

  "app/api/rag/latest/route.ts": async () => {
    signedOut();
    expect((await ragLatestGET(url(`/api/rag/latest?chatId=${FOREIGN_CHAT}`))).status).toBe(401);
    signedIn();
    for (const [qs, needle] of [[`chatId=${FOREIGN_CHAT}`, FOREIGN_CHAT], [`assistantMessageId=${FOREIGN_MESSAGE}`, FOREIGN_MESSAGE]] as const) {
      S.wheres.length = 0;
      const res = await ragLatestGET(url(`/api/rag/latest?${qs}&workspaceId=ws-B&userId=other`));
      expect(res.status).toBe(200);
      expect(await res.json()).toEqual({ trace: null }); // identical to a chat with no trace yet
      expectWhereHas(needle, USER_A, WS_A);
      expect(rendered().every((r) => !r.params.includes("ws-B") && !r.params.includes("other"))).toBe(true);
    }
    expect((await ragLatestGET(url("/api/rag/latest"))).status).toBe(400);
  },

  "app/api/sources/[id]/versions/route.ts": async () => {
    const meta = { name: "notes.md", size: 12, type: "text/markdown" };
    const post = (id: string) => versionsPOST(url(`/api/sources/${id}/versions`, { method: "POST", body: JSON.stringify(meta) }), ctx(id));
    signedOut();
    expect((await post(String(FOREIGN_SOURCE))).status).toBe(401);
    signedIn();
    vi.stubEnv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_x");
    S.sql.mockRejectedValue(Object.assign(new Error("SOURCE_NOT_FOUND"), { code: "P0002" })); // what scribe_reserve_source_version raises
    const res = await post(String(FOREIGN_SOURCE));
    expect(res.status).toBe(404);
    expect((await res.json()).code).toBe("source_not_found");
    const call = sqlCalls()[0];
    expect(call.text).toContain("scribe_reserve_source_version");
    expect(call.values.slice(0, 3)).toEqual([WS_A, FOREIGN_SOURCE, USER_A]); // session workspace, id, session user
    expect((await post("0")).status).toBe(400);
    expect((await post("abc")).status).toBe(400);
  },

  "app/api/sources/process-now/route.ts": async () => {
    const post = (body: unknown) => processNowPOST(url("/api/sources/process-now", { method: "POST", body: JSON.stringify(body) }));
    signedOut();
    expect((await post({ sourceId: FOREIGN_SOURCE })).status).toBe(401);
    signedIn();
    const foreign = await post({ sourceId: FOREIGN_SOURCE, workspaceId: "ws-B" });
    const unknown = await post({});
    expect(foreign.status).toBe(200);
    expect(await foreign.json()).toEqual(await unknown.json()); // a foreign id is a no-op with the identical response
    const q = sqlCalls()[0];
    expect(q.text).toContain('"workspace_id"');
    expect(q.values).toEqual([FOREIGN_SOURCE, WS_A]);
    expect(S.mutations).toEqual([]);
    // legacy engine: the runner is scoped to the session
    S.env.INGEST_ENGINE = "legacy";
    S.runQueuedSourceIntake.mockResolvedValue({ claimed: 0, ready: 0, retrying: 0, failed: 0 });
    await post({ sourceId: FOREIGN_SOURCE });
    expect(S.runQueuedSourceIntake).toHaveBeenCalledWith(1, { userId: USER_A, workspaceId: WS_A, sourceId: FOREIGN_SOURCE });
    expect((await post({ sourceId: -1 })).status).toBe(400);
  },

  "app/api/internal/ingest/run/route.ts": () => internalCases(runGET, "/api/internal/ingest/run", () => S.runQueuedSourceIntake),
  "app/api/internal/ingest/sweep/route.ts": () => internalCases(sweepGET, "/api/internal/ingest/sweep", () => S.runSweep),
  "app/api/internal/ingest/tick/route.ts": () => internalCases(tickGET, "/api/internal/ingest/tick", () => S.runStep),

  "app/api/billing/checkout/route.ts": () => sessionCase(() => billingCheckoutPOST(url("/api/billing/checkout", { method: "POST", body: "{}" }))),
  "app/api/billing/portal/route.ts": () => sessionCase(() => billingPortalPOST(url("/api/billing/portal", { method: "POST", body: "{}" }))),
  "app/api/billing/usage/route.ts": () => sessionCase(() => billingUsageGET()),
  "app/api/billing/verify/route.ts": () => sessionCase(() => billingVerifyPOST(url("/api/billing/verify", { method: "POST", body: "{}" }))),
  "app/api/sources/reserve/route.ts": () => sessionCase(() => reservePOST(url("/api/sources/reserve", { method: "POST", body: "{}" }))),

  "app/api/ingest/batch/route.ts": () => goneCase(batchRoot),
  "app/api/ingest/batch/init/route.ts": () => goneCase(batchInit),
  "app/api/ingest/batch/finalize/route.ts": () => goneCase(batchFinalize),
  "app/api/uploads/complete/route.ts": () => goneCase(uploadsComplete),
  "app/api/uploads/sign/route.ts": () => goneCase(uploadsSign),

  "app/api/health/route.ts": async () => {
    const body = await (await healthGET()).json();
    expect(Object.keys(body).sort()).toEqual(["db", "ok", "timestamp"]); // no versions, hosts or error text
  },

  "app/api/sources/upload-complete/route.ts": async () => {
    vi.stubEnv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_testStore_secret");
    const forged = {
      type: "blob.upload-completed",
      payload: { blob: { url: "https://x.private.blob.vercel-storage.com/a.md", pathname: "a.md" }, tokenPayload: JSON.stringify({ userId: "victim", sourceId: FOREIGN_SOURCE }) },
    };
    for (const headers of [{} as Record<string, string>, { "x-vercel-signature": "0".repeat(64) }]) {
      const res = await uploadCompletePOST(url("/api/sources/upload-complete", { method: "POST", headers, body: JSON.stringify(forged) }));
      expect(res.status).toBeGreaterThanOrEqual(400); // an unsigned or forged callback is refused...
    }
    expect(S.completeSourceUpload).not.toHaveBeenCalled(); // ...and never completes an upload for another user
    // the endpoint cannot be used to mint a client token either
    const token = await uploadCompletePOST(url("/api/sources/upload-complete", { method: "POST", body: JSON.stringify({ type: "blob.generate-client-token", payload: { pathname: "x.md", multipart: false, clientPayload: null } }) }));
    expect(token.status).toBeGreaterThanOrEqual(400);
  },

  "app/api/webhooks/razorpay/route.ts": async () => {
    vi.stubEnv("RAZORPAY_WEBHOOK_SECRET", "whsec_test");
    const body = JSON.stringify({ event: "subscription.activated", payload: { subscription: { entity: { notes: { userId: "victim" }, plan_id: "p" } } } });
    for (const sig of [null, "", "deadbeef", "0".repeat(64)]) {
      const res = await razorpayPOST(url("/api/webhooks/razorpay", { method: "POST", headers: sig === null ? {} : { "x-razorpay-signature": sig }, body }));
      expect(res.status).toBe(401);
    }
    expect(S.markPaymentEventIfNew).not.toHaveBeenCalled();
    expect(S.setUserSubscription).not.toHaveBeenCalled();
  },
};

async function internalCases(handler: (req: Request) => Promise<Response>, p: string, work: () => ReturnType<typeof vi.fn>) {
  const cases: Array<[string, Record<string, string>]> = [
    ["no header", {}],
    ["wrong secret", { authorization: "Bearer nope" }],
    ["same length wrong secret", { authorization: `Bearer ${"x".repeat("cron-secret".length)}` }],
    ["longer secret", { authorization: "Bearer cron-secret-and-more" }],
    ["wrong scheme", { authorization: "Basic cron-secret" }],
    ["bare secret", { authorization: "cron-secret" }],
    ["user bearer token", { authorization: "Bearer user_A" }],
  ];
  for (const [name, headers] of cases) {
    const res = await handler(url(p, { headers }));
    expect(res.status, name).toBe(401);
  }
  S.env.CRON_SECRET = undefined; // a deployment with no secret configured must fail closed, not open
  expect((await handler(url(p, { headers: { authorization: "Bearer " } }))).status).toBe(401);
  expect((await handler(url(p, { headers: { authorization: "Bearer undefined" } }))).status).toBe(401);
  expect((await handler(url(p))).status).toBe(401);
  await drain();
  expect(work()).not.toHaveBeenCalled();
  expect(S.fireTick).not.toHaveBeenCalled();
}

async function sessionCase(call: () => Promise<Response>) {
  signedOut();
  expect((await call()).status).toBe(401);
}

async function goneCase(mod: Record<string, unknown>) {
  for (const method of ["GET", "POST", "PUT", "DELETE"]) {
    const res = await (mod[method] as () => Promise<Response>)();
    expect(res.status, method).toBe(410);
  }
}

describe("route sweep", () => {
  it("has a handler for every table row and no handler without a row", () => {
    expect(Object.keys(ROUTE_HANDLERS).sort()).toEqual(Object.keys(ROUTE_TABLE).sort());
  });

  it.each(Object.entries(ROUTE_TABLE))("%s", async (file, row) => {
    expect(row.note.length).toBeGreaterThan(10);
    await ROUTE_HANDLERS[file]();
  });
});

// ------------------------------------------------------------------------------------------------ server actions

type Fn = (...args: never[]) => Promise<unknown>;
const ACTION_CASES: Record<string, Record<string, () => Promise<void>>> = {
  "app/chat/actions.ts": {
    getChats: async () => {
      signedOut();
      expect(await chatActions.getChats()).toEqual([]);
    },
    createChat: async () => {
      signedOut();
      await expect(chatActions.createChat("t")).rejects.toThrow();
      expect(S.mutations).toEqual([]);
    },
    getChatMessages: async () => {
      expect(await chatActions.getChatMessages(FOREIGN_CHAT)).toEqual([]);
      expectWhereHas(FOREIGN_CHAT, USER_A, WS_A);
    },
    deleteChat: async () => {
      expect(await chatActions.deleteChat(FOREIGN_CHAT)).toBe(false);
      expectWhereHas(FOREIGN_CHAT, USER_A, WS_A);
      expect(S.mutations).toEqual([]); // nothing deleted
    },
    getChatVerifications: async () => {
      expect(await chatActions.getChatVerifications(FOREIGN_CHAT)).toEqual({});
      expect(S.callIntelligence).not.toHaveBeenCalled(); // no message ids of a foreign chat are ever sent on
    },
  },
  "app/files/actions.ts": {
    getFiles: async () => {
      signedOut();
      expect(await filesActions.getFiles()).toEqual([]);
    },
    deleteFile: async () => {
      S.deleteSource.mockResolvedValue(false);
      expect(await filesActions.deleteFile(FOREIGN_SOURCE)).toMatchObject({ success: false });
      expect(S.deleteSource).toHaveBeenCalledWith(USER_A, FOREIGN_SOURCE);
      signedOut();
      expect(await filesActions.deleteFile(FOREIGN_SOURCE)).toMatchObject({ success: false });
      expect(S.deleteSource).toHaveBeenCalledTimes(1);
    },
    retrySource: async () => {
      S.sql.mockResolvedValue([{ n: 0 }]);
      expect(await filesActions.retrySource(FOREIGN_SOURCE)).toMatchObject({ success: false });
      const q = sqlCalls()[0];
      expect(q.values).toContain(WS_A);
      expect(q.values).toContain(FOREIGN_SOURCE);
      expect(q.text).toMatch(/"workspace_id" = \?/);
      expect(S.fireTick).not.toHaveBeenCalled();
    },
    getSourceVersions: async () => {
      expect(await filesActions.getSourceVersions(FOREIGN_SOURCE)).toEqual([]);
      expectScoped(sqlCalls(), FOREIGN_SOURCE);
      expect(await filesActions.getSourceVersions(-5)).toEqual([]);
    },
  },
  "app/files/[id]/actions.ts": {
    getFileWithChunks: async () => {
      expect(await detailActions.getFileWithChunks(FOREIGN_SOURCE)).toBeNull();
      expectWhereHas(FOREIGN_SOURCE, USER_A, WS_A);
    },
    getSourceOverview: () => detail(() => detailActions.getSourceOverview(FOREIGN_SOURCE)),
    getSourceStructure: () => detail(() => detailActions.getSourceStructure(FOREIGN_SOURCE)),
    getSourceChunks: () => detail(() => detailActions.getSourceChunks(FOREIGN_SOURCE)),
    getSourceHistory: () => detail(() => detailActions.getSourceHistory(FOREIGN_SOURCE)),
    getSourceVersionsPage: () => detail(() => detailActions.getSourceVersionsPage(FOREIGN_SOURCE)),
  },
};

async function detail(call: () => Promise<{ ok: boolean; status?: number }>) {
  const foreign = await call();
  expect(foreign).toMatchObject({ ok: false, status: 404 });
  expectScoped(sqlCalls(), FOREIGN_SOURCE); // the first query is the scoped ownership lookup
  expect(sqlCalls()).toHaveLength(1); // and nothing else ran once it found no Source
  S.sql.mockClear();
  signedOut();
  expect(await call()).toMatchObject({ ok: false, status: 401 });
  expect(S.sql).not.toHaveBeenCalled();
}

describe("server action sweep", () => {
  it("has a case for every exported action and no case for one that does not exist", () => {
    const listed = Object.fromEntries(Object.entries(ACTION_TABLE).map(([f, a]) => [f, Object.keys(a).sort()]));
    const cases = Object.fromEntries(Object.entries(ACTION_CASES).map(([f, a]) => [f, Object.keys(a).sort()]));
    expect(cases).toEqual(listed);
    const modules: Record<string, Record<string, unknown>> = {
      "app/chat/actions.ts": chatActions, "app/files/actions.ts": filesActions, "app/files/[id]/actions.ts": detailActions,
    };
    for (const [file, names] of Object.entries(listed)) {
      expect(Object.keys(modules[file]).sort(), file).toEqual(names);
      for (const n of names) expect(typeof (modules[file][n] as Fn)).toBe("function");
    }
  });

  const rows = Object.entries(ACTION_CASES).flatMap(([file, actions]) => Object.entries(actions).map(([name, run]) => [`${file} ${name}`, run] as const));
  it.each(rows)("%s", async (_name, run) => {
    await run();
  });
});
