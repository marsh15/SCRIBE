import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  claims: { userId: null as string | null, sessionClaims: undefined as unknown },
  dbQueries: 0,
}));

vi.mock("@clerk/nextjs/server", () => ({ auth: async () => h.claims }));

// Every query chain resolves to one row; the counter shows whether the page touched the database at all.
vi.mock("@/lib/db-config", () => {
  const rows = [{ count: 7 }];
  const chain = () => {
    h.dbQueries += 1;
    const q: { from: () => typeof q; where: () => typeof q; then: (r: (v: unknown) => unknown) => unknown } = {
      from: () => q,
      where: () => q,
      then: (resolve) => resolve(rows),
    };
    return q;
  };
  return { db: { select: chain } };
});

vi.mock("next/navigation", () => ({
  notFound: () => {
    throw new Error("NEXT_NOT_FOUND");
  },
}));

import { isAdmin } from "@/lib/auth";
import AdminAnalyticsPage from "@/app/admin/analytics/page";

describe("/admin/analytics access", () => {
  beforeEach(() => {
    h.claims = { userId: null, sessionClaims: undefined };
    h.dbQueries = 0;
  });

  it("isAdmin is false when signed out, without a role claim, and for the user role", async () => {
    expect(await isAdmin()).toBe(false);
    h.claims = { userId: "user_1", sessionClaims: undefined };
    expect(await isAdmin()).toBe(false);
    h.claims = { userId: "user_1", sessionClaims: { metadata: {} } };
    expect(await isAdmin()).toBe(false);
    h.claims = { userId: "user_1", sessionClaims: { metadata: { role: "user" } } };
    expect(await isAdmin()).toBe(false);
  });

  it("isAdmin is true only for role admin on a signed-in session", async () => {
    h.claims = { userId: "user_1", sessionClaims: { metadata: { role: "admin" } } };
    expect(await isAdmin()).toBe(true);
    h.claims = { userId: null, sessionClaims: { metadata: { role: "admin" } } };
    expect(await isAdmin()).toBe(false);
  });

  it("returns 404 and runs no query for a signed-in non-admin", async () => {
    h.claims = { userId: "user_1", sessionClaims: { metadata: { role: "user" } } };
    await expect(AdminAnalyticsPage()).rejects.toThrow("NEXT_NOT_FOUND");
    expect(h.dbQueries).toBe(0);
  });

  it("renders the metrics for an admin", async () => {
    h.claims = { userId: "user_1", sessionClaims: { metadata: { role: "admin" } } };
    const tree = await AdminAnalyticsPage();
    expect(tree).toBeTruthy();
    expect(h.dbQueries).toBe(5);
  });
});
