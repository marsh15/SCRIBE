import { beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({ userId: vi.fn(), deleteSource: vi.fn() }));
vi.mock("@/lib/auth", () => ({ getUserId: m.userId, isNotAuthenticatedError: (e: unknown) => e instanceof Error && e.message === "Not authenticated" }));
vi.mock("@/lib/ingestion/source-intake", () => ({ deleteSource: m.deleteSource }));
vi.mock("@/lib/api-errors", () => ({ internalError: () => Response.json({ error: "internal" }, { status: 500 }) }));

import { DELETE } from "@/app/api/sources/[id]/route";

const call = (id: string) => DELETE(new Request("http://x", { method: "DELETE" }), { params: Promise.resolve({ id }) });

beforeEach(() => {
  vi.clearAllMocks();
  m.userId.mockResolvedValue("user_1");
});

describe("DELETE /api/sources/[id]", () => {
  it("deletes the caller's Source, keyed by the server-derived user", async () => {
    m.deleteSource.mockResolvedValue(true);
    const res = await call("42");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    expect(m.deleteSource).toHaveBeenCalledWith("user_1", 42);
  });

  it("answers 404 for a Source that is unknown, foreign or already deleted", async () => {
    m.deleteSource.mockResolvedValue(false);
    expect((await call("42")).status).toBe(404);
  });

  it.each(["0", "-3", "1.5", "abc"])("rejects the id %s without touching the database", async (id) => {
    expect((await call(id)).status).toBe(400);
    expect(m.deleteSource).not.toHaveBeenCalled();
  });

  it("answers 401 when signed out", async () => {
    m.userId.mockRejectedValue(new Error("Not authenticated"));
    expect((await call("42")).status).toBe(401);
    expect(m.deleteSource).not.toHaveBeenCalled();
  });
});
