import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { sql as rawSql } from "drizzle-orm";
import { PgDialect } from "drizzle-orm/pg-core";

/**
 * Real-Postgres test of lib/scoped.ts under RLS (drizzle/0013). Skipped unless DATABASE_URL_TEST points at a throwaway
 * SUPERUSER test database that has the schema and migrations applied (scripts/apply-migrations.sh) and `psql` is on
 * PATH. The production `db` is neon-http (HTTP, not reachable locally), so this test replaces `@/lib/db-config` with a
 * shim that renders each drizzle query to SQL and runs the whole batch in ONE psql transaction as `scribe_app`
 * (SET LOCAL ROLE), which is the same atomicity contract Neon gives db.batch(). The code under test (scoped(), its
 * set_config statement and result stripping) is the real module.
 */
const url = process.env.DATABASE_URL_TEST;
function hasPsql() {
  try {
    execFileSync("psql", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}
const enabled = Boolean(url) && hasPsql() && /test/i.test(url ?? "");

const dialect = new PgDialect();
// scoped().batch() is typed for drizzle query builders; raw sql fragments are enough here.
const q = (s: unknown) => s as never;
const lit = (v: unknown) => (v === null || v === undefined ? "NULL" : `'${String(v).replace(/'/g, "''")}'`);
function render(item: unknown): string {
  const q = dialect.sqlToQuery(item as never);
  return q.sql.replace(/\$(\d+)/g, (_m, i) => lit(q.params[Number(i) - 1]));
}
function psql(input: string, asAppRole: boolean) {
  // -1 wraps the whole script in one transaction; SET LOCAL ROLE lasts until it ends.
  const script = asAppRole ? `SET LOCAL ROLE scribe_app;\n${input}` : input;
  return execFileSync("psql", [url!, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-1"], {
    input: script,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  }).trim();
}

const h = vi.hoisted(() => ({ run: (_items: unknown[]) => [] as string[][] }));
vi.mock("@/lib/db-config", () => ({
  db: {
    execute: (q: unknown) => q,
    batch: async (items: unknown[]) => h.run(items),
  },
}));

import { scoped } from "@/lib/scoped";

// Run the rendered statements inside one transaction as scribe_app; each statement's output is bracketed by markers.
h.run = (items) => {
  const body = items.map((it, i) => `\\echo @@${i}\n${render(it)};`).join("\n");
  const out = psql(body, true);
  const parts = out.split(/^@@\d+$/m).slice(1);
  return parts.map((p) => p.trim().split("\n").filter(Boolean));
};

describe.skipIf(!enabled)("scoped() under RLS (real Postgres, scribe_app)", () => {
  const wsA = randomUUID();
  const wsB = randomUUID();
  const tag = `scoped-it-${randomUUID()}`;

  beforeAll(() => {
    psql(
      `INSERT INTO workspaces(id, kind, owner_user_id) VALUES ('${wsA}','eval','${tag}-a'),('${wsB}','eval','${tag}-b');
       INSERT INTO files(name,type,size,user_id,workspace_id,status) VALUES
         ('${tag}-a1','text/plain',1,'u','${wsA}','ready'),('${tag}-a2','text/plain',1,'u','${wsA}','ready'),
         ('${tag}-b1','text/plain',1,'u','${wsB}','ready');`,
      false,
    );
  });
  afterAll(() => {
    psql(`DELETE FROM files WHERE workspace_id IN ('${wsA}','${wsB}'); DELETE FROM workspaces WHERE id IN ('${wsA}','${wsB}');`, false);
  });

  const names = (rows: string[]) => rows.sort();

  it("a scoped batch sees only its own workspace's rows", async () => {
    const [a] = (await scoped(wsA).batch([q(rawSql`select name from files where name like ${tag + "%"}`)])) as string[][];
    expect(names(a)).toEqual([`${tag}-a1`, `${tag}-a2`]);
    const [b] = (await scoped(wsB).batch([q(rawSql`select name from files where name like ${tag + "%"}`)])) as string[][];
    expect(names(b)).toEqual([`${tag}-b1`]);
  });

  it("without the context scribe_app sees nothing (fail closed) and the context does not leak between batches", async () => {
    await scoped(wsA).batch([q(rawSql`select 1`)]);
    const out = h.run([q(rawSql`select name from files where name like ${tag + "%"}`)]);
    expect(out[0]).toEqual([]);
  });

  it("returns only the caller's results (set_config result stripped)", async () => {
    const res = await scoped(wsA).batch([q(rawSql`select count(*) from files where name like ${tag + "%"}`), q(rawSql`select 'x'`)]);
    expect(res).toEqual([["2"], ["x"]]);
  });

  it("cannot write into another workspace (WITH CHECK) and the failed batch applies nothing", async () => {
    await expect(
      scoped(wsA).batch([
        q(rawSql`insert into files(name,type,size,user_id,workspace_id,status) values (${tag + "-bad"},'text/plain',1,'u',${wsB},'ready')`),
      ]),
    ).rejects.toThrow();
    expect(psql(`select count(*) from files where name = '${tag}-bad'`, false)).toBe("0");
  });
});
