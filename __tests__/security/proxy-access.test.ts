/**
 * T27: which paths skip Clerk's session check. The Python service and the cron/tick endpoints are called server to
 * server without a Clerk session, so they MUST be public here (each authenticates itself); every other API path must
 * still be protected. A regression either way fails: a protected /api/py/* silently breaks retrieval in production, a
 * public /api/chat would be an open door.
 */
import { describe, expect, it, vi } from "vitest";

const protect = vi.fn();
vi.mock("@clerk/nextjs/server", () => ({
  // path-to-regexp style "(.*)" patterns are plain regular expressions for our purposes
  createRouteMatcher: (patterns: string[]) => (req: { nextUrl: { pathname: string } }) =>
    patterns.some((p) => new RegExp(`^${p}$`).test(req.nextUrl.pathname)),
  clerkMiddleware: (handler: unknown) => handler,
}));

import proxy from "@/proxy";

const handler = proxy as unknown as (auth: { protect: () => Promise<void> }, req: unknown) => Promise<unknown>;
async function isProtected(pathname: string, method = "GET") {
  protect.mockClear();
  await handler({ protect }, { method, headers: new Headers(), nextUrl: { pathname } });
  return protect.mock.calls.length > 0;
}

describe("proxy.ts public paths", () => {
  it.each([
    "/api/py/retrieve", "/api/py/health", "/api/py/ingest/step", "/api/py/evidence/open", "/api/py/verify", "/api/py/compare",
    "/api/internal/ingest/tick", "/api/internal/ingest/sweep", "/api/internal/ingest/run",
    "/api/webhooks/razorpay", "/api/sources/upload-complete", "/api/health", "/sign-in", "/sign-up/x",
  ])("%s skips the Clerk session check (it authenticates itself)", async (p) => {
    expect(await isProtected(p, "POST")).toBe(false);
  });

  it.each([
    "/api/chat", "/api/compare", "/compare", "/eval", "/eval/compare", "/eval/22222222-2222-4222-8222-222222222222", "/api/evidence/22222222-2222-4222-8222-222222222222", "/api/files/1/view", "/api/rag/latest",
    "/api/sources/reserve", "/api/sources/process-now", "/api/sources/7/versions", "/api/billing/usage",
    "/api/billing/checkout", "/api/ingest/batch", "/api/uploads/sign", "/chat/abc", "/files/1", "/upload",
    "/api/pyx", "/api/internal/ingest/other", "/api/internal", "/api/internal/anything-else",
  ])("%s requires a signed-in user", async (p) => {
    expect(await isProtected(p, "POST")).toBe(true);
  });
});
