import { afterEach, describe, expect, it, vi } from "vitest";

describe("database configuration", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("allows module loading but fails clearly when an unconfigured database is used", async () => {
    vi.stubEnv("NEON_DATABASE_URL", "");
    vi.resetModules();

    const { db, sql } = await import("@/lib/db-config");

    expect(() => db.query).toThrow(/NEON_DATABASE_URL is not configured/);
    expect(() => sql`SELECT 1`).toThrow(/NEON_DATABASE_URL is not configured/);
  });
});
