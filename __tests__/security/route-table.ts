/**
 * T27: the single source of truth for the access-control sweep. Every `app/api/**\/route.ts` file and every exported
 * server action must be listed here (route-guard.test.ts fails otherwise) and every row marked with a sweep category is
 * exercised by idor-sweep.test.ts, which fails when a row has no handler.
 *
 * Categories
 *  - idor        takes an id (path, query or body) that must belong to the caller's workspace and user: a foreign id is
 *                indistinguishable from an unknown one (404, or the documented empty result).
 *  - internal    cron / self-fire endpoints guarded by `Authorization: Bearer ${CRON_SECRET}`: 401 without it.
 *  - session     needs a signed-in user but takes no resource id: 401 when signed out.
 *  - gone        removed endpoints (410 on every method).
 *  - public      reachable without a session by design; the reason says what protects it.
 */
export type RouteCategory = "idor" | "internal" | "session" | "admin" | "gone" | "public";

export const ROUTE_TABLE: Record<string, { category: RouteCategory; note: string }> = {
  "app/api/chat/route.ts": { category: "idor", note: "chatId query parameter; 404 for a chat the caller does not own" },
  "app/api/compare/route.ts": { category: "idor", note: "optional source ids in the body; workspace and user come from the session only, a foreign id yields empty rankings" },
  "app/api/evidence/[id]/route.ts": { category: "idor", note: "evidence item id; Python re-checks the workspace on every open (INV-8)" },
  "app/api/files/[id]/view/route.ts": { category: "idor", note: "source id and ?version=; scoped by workspace and user" },
  "app/api/rag/latest/route.ts": { category: "idor", note: "chatId / assistantMessageId; a foreign id yields the same empty trace as an unknown one" },
  "app/api/sources/[id]/versions/route.ts": { category: "idor", note: "source id; the reserve SQL function refuses a foreign source" },
  "app/api/sources/process-now/route.ts": { category: "idor", note: "sourceId in the body; a foreign id is a no-op identical to an unknown one" },
  "app/api/internal/ingest/run/route.ts": { category: "internal", note: "CRON_SECRET bearer" },
  "app/api/internal/ingest/sweep/route.ts": { category: "internal", note: "CRON_SECRET bearer" },
  "app/api/internal/ingest/tick/route.ts": { category: "internal", note: "CRON_SECRET bearer" },
  "app/api/admin/migration/route.ts": { category: "admin", note: "owner-only migration totals and retry for structural-v1" },
  "app/api/billing/checkout/route.ts": { category: "session", note: "no resource id; user-scoped" },
  "app/api/billing/portal/route.ts": { category: "session", note: "no resource id; user-scoped" },
  "app/api/billing/usage/route.ts": { category: "session", note: "no resource id; user-scoped" },
  "app/api/billing/verify/route.ts": { category: "session", note: "payment ids verified by HMAC signature; user-scoped" },
  "app/api/sources/reserve/route.ts": { category: "session", note: "creates a new Source for the caller; no existing id" },
  "app/api/ingest/batch/route.ts": { category: "gone", note: "removed in T04" },
  "app/api/ingest/batch/init/route.ts": { category: "gone", note: "removed in T04" },
  "app/api/ingest/batch/finalize/route.ts": { category: "gone", note: "removed in T04" },
  "app/api/uploads/complete/route.ts": { category: "gone", note: "removed in T04" },
  "app/api/uploads/sign/route.ts": { category: "gone", note: "removed in T04" },
  "app/api/health/route.ts": { category: "public", note: "liveness probe; returns only ok/db/timestamp" },
  "app/api/sources/upload-complete/route.ts": { category: "public", note: "Vercel Blob callback; handleUpload verifies the signature, no client token is issued here" },
  "app/api/webhooks/razorpay/route.ts": { category: "public", note: "Razorpay webhook; HMAC signature verified before any work" },
};

/** Routes that are public in proxy.ts (no Clerk session). Each must be justified by a `public`/`internal` row above. */
export const PUBLIC_PATHS = [
  "/api/webhooks/razorpay",
  "/api/sources/upload-complete",
  "/api/internal/ingest/run",
  "/api/internal/ingest/sweep",
  "/api/internal/ingest/tick",
  "/api/health",
  "/api/py/*",
];

export type ActionKind = "idor" | "session";

/** Every exported function of a `"use server"` file. `idor` actions take a resource id. */
export const ACTION_TABLE: Record<string, Record<string, ActionKind>> = {
  "app/chat/actions.ts": {
    getChats: "session",
    createChat: "session",
    getChatMessages: "idor",
    deleteChat: "idor",
    getChatVerifications: "idor",
  },
  "app/eval/actions.ts": {
    getEvalAccess: "session",
  },
  "app/files/actions.ts": {
    getFiles: "session",
    deleteFile: "idor",
    retrySource: "idor",
    getSourceVersions: "idor",
  },
  "app/files/[id]/actions.ts": {
    getFileWithChunks: "idor",
    getSourceOverview: "idor",
    getSourceStructure: "idor",
    getSourceChunks: "idor",
    getSourceHistory: "idor",
    getSourceVersionsPage: "idor",
  },
};
