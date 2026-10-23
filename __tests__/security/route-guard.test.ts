/**
 * T27 guard: a new API route, server action or public (unauthenticated) path must be added to the access-control sweep
 * table (route-table.ts) before CI goes green. Nothing here executes a route; it reads the file system and proxy.ts.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ACTION_TABLE, PUBLIC_PATHS, ROUTE_TABLE } from "./route-table";

const ROOT = path.resolve(__dirname, "../..");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next" || name.startsWith(".")) continue;
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}
const rel = (p: string) => path.relative(ROOT, p).split(path.sep).join("/");

describe("route guard", () => {
  const routeFiles = walk(path.join(ROOT, "app/api")).map(rel).filter((f) => /\/route\.tsx?$/.test(f)).sort();

  it("every app/api route file is in the sweep table, and the table lists no route that does not exist", () => {
    const listed = Object.keys(ROUTE_TABLE).sort();
    const unlisted = routeFiles.filter((f) => !listed.includes(f));
    const stale = listed.filter((f) => !routeFiles.includes(f));
    expect(unlisted, `add these routes to __tests__/security/route-table.ts (and to idor-sweep.test.ts if they take an id): ${unlisted}`).toEqual([]);
    expect(stale, `remove from route-table.ts: ${stale}`).toEqual([]);
  });

  it("every route that takes a dynamic segment or an id-like parameter is classified idor or admin", () => {
    for (const f of routeFiles) {
      const src = readFileSync(path.join(ROOT, f), "utf8");
      const takesId =
        f.includes("[") ||
        /searchParams\.get\(["'](chatId|assistantMessageId|fileId|sourceId|id|version)["']\)/.test(src) ||
        /\bsourceId\b/.test(src);
      if (takesId) {
        expect(["idor", "admin"], `${f} takes an id: it must be scoped or owner-only`).toContain(ROUTE_TABLE[f]?.category);
      }
    }
  });

  it("server actions: every file with 'use server' and every exported function is in the action table", () => {
    const serverFiles = [...walk(path.join(ROOT, "app")), ...walk(path.join(ROOT, "lib")), ...walk(path.join(ROOT, "components"))]
      .filter((f) => /\.(ts|tsx)$/.test(f))
      .filter((f) => /^\s*["']use server["']/m.test(readFileSync(f, "utf8").slice(0, 200)))
      .map(rel)
      .sort();
    expect(serverFiles).toEqual(Object.keys(ACTION_TABLE).sort());
    for (const f of serverFiles) {
      const src = readFileSync(path.join(ROOT, f), "utf8");
      const exported = [...src.matchAll(/^export\s+(?:async\s+)?function\s+(\w+)/gm)].map((m) => m[1]).sort();
      expect(exported, f).toEqual(Object.keys(ACTION_TABLE[f]).sort());
    }
  });

  it("proxy.ts public routes are exactly the documented set and each is justified in the table", () => {
    const proxy = readFileSync(path.join(ROOT, "proxy.ts"), "utf8");
    const block = proxy.slice(proxy.indexOf("createRouteMatcher(["), proxy.indexOf("]);"));
    const patterns = [...block.matchAll(/"([^"]+)"/g)].map((m) => m[1]).filter((p) => p.startsWith("/api"));
    const normalised = patterns.map((p) => p.replace(/\/\(\.\*\)$/, "/*").replace(/\(\.\*\)$/, "/*")).sort();
    expect(normalised).toEqual([...PUBLIC_PATHS].sort());
    for (const p of PUBLIC_PATHS) {
      if (p === "/api/py/*") continue; // authenticated by the internal token inside the Python service (token matrix test)
      const file = `app${p}/route.ts`;
      expect(["public", "internal"], `${p} is public in proxy.ts but not justified in the table`).toContain(ROUTE_TABLE[file]?.category);
    }
  });

  it("every internal route is public in proxy.ts (cron and self-fired ticks carry no Clerk session)", () => {
    for (const [file, row] of Object.entries(ROUTE_TABLE)) {
      if (row.category !== "internal") continue;
      expect(PUBLIC_PATHS, file).toContain(file.replace(/^app/, "").replace(/\/route\.ts$/, ""));
    }
  });
});
