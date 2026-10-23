import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  claims: { userId: null as string | null, sessionClaims: undefined as unknown },
  query: vi.fn(),
  sqlRows: [] as unknown[],
  retry: vi.fn(),
  after: vi.fn(),
  fireTick: vi.fn(),
  env: { INGEST_ENGINE: "python" },
}));

vi.mock("@clerk/nextjs/server", () => ({ auth: async () => h.claims }));
vi.mock("@/lib/db-config", () => ({ sql: { query: h.query } }));
vi.mock("@/lib/ingestion/python-queue", () => ({ requeueFailedBackfillJob: h.retry }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: h.fireTick }));
vi.mock("@/lib/env", () => ({ env: h.env }));
vi.mock("next/server", async () => ({
  ...(await vi.importActual<typeof import("next/server")>("next/server")),
  after: h.after,
}));
vi.mock("next/navigation", () => ({ notFound: () => { throw new Error("NEXT_NOT_FOUND"); } }));

import { GET, POST } from "@/app/api/admin/migration/route";
import { progressPercent } from "@/app/admin/migration/progress";
import AdminMigrationPage from "@/app/admin/migration/page";

const admin = () => { h.claims = { userId: "user_admin", sessionClaims: { metadata: { role: "admin" } } }; };

describe("admin migration progress", () => {
  beforeEach(() => {
    h.claims = { userId: "user_regular", sessionClaims: { metadata: { role: "user" } } };
    h.query.mockReset();
    h.query.mockResolvedValue([]);
    h.retry.mockReset();
    h.after.mockReset();
    h.fireTick.mockReset();
  });

  it("returns 403 for a non-admin before querying any migration data", async () => {
    const response = await GET();
    expect(response.status).toBe(403);
    expect(h.query).not.toHaveBeenCalled();
  });

  it("hides the page from non-admins before any data lookup", async () => {
    await expect(AdminMigrationPage()).rejects.toThrow("NEXT_NOT_FOUND");
    expect(h.query).not.toHaveBeenCalled();
  });

  it("returns generation counts, stages and honest unknown progress", async () => {
    admin();
    h.query.mockResolvedValue([
      { source_id: 4, name: "known.pdf", active_version_id: "v1", desired_version_id: "v1", storage_url: "blob", reupload_required_at: null,
        job_id: 8, job_status: "processing", job_stage: "EMBED", error_code: null, last_error: null,
        generation_id: "g1", embedded_chunks: 3, expected_chunks: 4, state: "building" },
      { source_id: 5, name: "unknown.pdf", active_version_id: "v2", desired_version_id: "v2", storage_url: "blob", reupload_required_at: null,
        job_id: null, job_status: null, job_stage: null, error_code: null, last_error: null,
        generation_id: null, embedded_chunks: 0, expected_chunks: 0, state: "pending" },
    ]);
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      profile: "structural-v1",
      sources: [
        { sourceId: 4, stage: "EMBED", embeddedChunks: 3, expectedChunks: 4, progressPct: 75 },
        { sourceId: 5, stage: "PENDING", embeddedChunks: 0, expectedChunks: 0, progressPct: null },
      ],
      totals: { sources: 2, embeddedChunks: 3, expectedChunks: 4, progressPct: 75, failures: 0 },
    });
    expect(h.query.mock.calls[0][0]).toContain("profile.name = 'structural-v1'");
    expect(h.query.mock.calls[0][0]).toContain("job.generation_id = g.id");
  });

  it("returns retry conflicts for unsafe jobs and kicks the current tick chain after a valid retry", async () => {
    admin();
    h.retry.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
    const makeRequest = () => new Request("https://scribe.example/api/admin/migration", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sourceId: 4 }),
    });
    expect((await POST(makeRequest())).status).toBe(409);
    expect(h.after).not.toHaveBeenCalled();
    expect((await POST(makeRequest())).status).toBe(200);
    expect(h.after).toHaveBeenCalledTimes(1);
    expect(h.retry).toHaveBeenCalledWith(4);
  });

  it.each([
    ["boolean source id", { sourceId: true }],
    ["array source id", { sourceId: [4] }],
    ["null body", null],
    ["unsafe integer", { sourceId: Number.MAX_SAFE_INTEGER + 1 }],
  ])("rejects %s before querying or retrying", async (_label, body) => {
    admin();
    const response = await POST(new Request("https://scribe.example/api/admin/migration", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }));
    expect(response.status).toBe(400);
    expect(h.query).not.toHaveBeenCalled();
    expect(h.retry).not.toHaveBeenCalled();
  });

  it("keeps progress percentages bounded and leaves zero expected chunks unknown", () => {
    expect(progressPercent(1, 0)).toBeNull();
    expect(progressPercent(1, 3)).toBe(33.3);
    expect(progressPercent(4, 3)).toBe(100);
  });
});
