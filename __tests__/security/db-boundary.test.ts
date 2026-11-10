import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Structural guard for the database roles (INV-1, drizzle/0013).
 *
 * `db` is the runtime connection: once it logs in as `scribe_app`, every workspace table needs the context set by
 * `scoped()`. `adminSql` / `adminDb` are the owner connection for work that cannot run inside one workspace. They may
 * only be imported by the modules below, each with the reason. Adding a caller means adding a line here, in review.
 */
const ROOT = join(__dirname, "..", "..");
const ADMIN_ALLOW_LIST: Record<string, string> = {
  "app/admin/analytics/page.tsx": "platform-wide counts across every workspace (admin only)",
  "app/api/admin/migration/route.ts": "migration dashboard scan across every user's Sources (admin only)",
  "lib/db-compat.ts": "runtime schema repair (ALTER TABLE) needs the owner role",
  "lib/eval/queries.ts": "global eval tables and the viewer's eval-workspace membership across workspaces",
  "lib/ingestion/python-queue.ts": "requeueFailedBackfillJob is the admin dashboard's cross-workspace retry",
  "lib/ingestion/source-intake.ts": "legacy-engine cron worker: claims queued jobs across workspaces (scope-less run only)",
};

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (["node_modules", ".next", ".git", "__tests__", "brag-output", ".claude", "intelligence", "benchmarks", "fixtures"].includes(name)) continue;
    const path = join(dir, name);
    if (statSync(path).isDirectory()) sourceFiles(path, out);
    else if (/\.(ts|tsx)$/.test(name)) out.push(path);
  }
  return out;
}

const files = ["app", "lib", "components", "scripts"].flatMap((d) => {
  try {
    return sourceFiles(join(ROOT, d));
  } catch {
    return [];
  }
});
const rel = (f: string) => relative(ROOT, f).split("\\").join("/");

describe("database role boundary", () => {
  it("scans the source tree", () => {
    expect(files.length).toBeGreaterThan(100);
  });

  it("only allow-listed modules import the owner connection", () => {
    const importers = files
      .filter((f) => rel(f) !== "lib/db-config.ts")
      .filter((f) => /\badmin(Sql|Db)\b/.test(readFileSync(f, "utf8")))
      .map(rel)
      .sort();
    expect(importers).toEqual(Object.keys(ADMIN_ALLOW_LIST).sort());
  });

  it("no module can run a raw statement outside a workspace context", () => {
    const importsRawSql = (source: string) =>
      [...source.matchAll(/import\s*\{([^}]*)\}\s*from\s*["'][^"']*db-config["']/g)].some((m) =>
        m[1].split(",").some((spec) => spec.trim().split(/\s+as\s+/)[0] === "sql")
      );
    const offenders = files
      .filter((f) => rel(f) !== "lib/db-config.ts")
      .filter((f) => importsRawSql(readFileSync(f, "utf8")))
      .map(rel);
    expect(offenders).toEqual([]);
  });

  it("every allow-listed module still exists (no stale entries)", () => {
    for (const path of Object.keys(ADMIN_ALLOW_LIST)) {
      expect(files.map(rel)).toContain(path);
    }
  });
});
