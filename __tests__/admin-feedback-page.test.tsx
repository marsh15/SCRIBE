import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ admin: false, queries: 0 }));
vi.mock("@/lib/auth", () => ({ isAdmin: async () => h.admin }));
vi.mock("@/lib/db-config", () => {
  const query = {
    from: () => query,
    innerJoin: () => query,
    orderBy: () => query,
    limit: async () => { h.queries += 1; return []; },
  };
  return { db: { select: () => query } };
});
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("NEXT_NOT_FOUND"); } }));

import AdminFeedbackPage from "@/app/admin/feedback/page";

describe("/admin/feedback access", () => {
  beforeEach(() => { h.admin = false; h.queries = 0; });

  it("hides the page from non-admins before querying feedback", async () => {
    await expect(AdminFeedbackPage()).rejects.toThrow("NEXT_NOT_FOUND");
    expect(h.queries).toBe(0);
  });

  it("loads the newest feedback list for admins", async () => {
    h.admin = true;
    const page = await AdminFeedbackPage();
    expect(page).toBeTruthy();
    expect(h.queries).toBe(1);
  });
});
