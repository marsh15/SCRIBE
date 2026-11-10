import { execFileSync } from "node:child_process";

/**
 * Shared pieces for tests that run the app's real database code against a local PostgreSQL as a real non-owner login
 * (scripts/test-chat-rls.py creates the login and a throwaway database). The production `db` is neon-http (HTTP); here a
 * drizzle pg-proxy sends each statement, or each `batch`, through __tests__/support/pg_parameterized.py in ONE
 * transaction, which is the atomicity contract Neon gives db.batch(). Rows from raw `db.execute()` also carry their
 * column names (as neon-http returns objects there), while query-builder rows stay positional.
 */
export interface Harness {
  appUrl: string;
  adminUrl: string;
  contextAfter: string;
  queued: null | Array<{ sql: string; params: unknown[]; resolve: (r: { rows: unknown[] }) => void; reject: (e: Error) => void }>;
  lastColumns: string[][];
}

export function bridgeEnabled(prefix = "CHAT_RLS") {
  const appUrl = process.env[`${prefix}_DATABASE_URL`];
  const adminUrl = process.env[`${prefix}_ADMIN_URL`];
  const local = (url: string) => ["localhost", "127.0.0.1", "[::1]"].includes(new URL(url).hostname) && new URL(url).pathname.endsWith("_test");
  return !!appUrl && !!adminUrl && local(appUrl) && local(adminUrl) && new URL(appUrl).pathname === new URL(adminUrl).pathname;
}

export function runBatch(h: Harness, queries: { sql: string; params: unknown[] }[], admin = false): unknown[][][] {
  const input = JSON.stringify({ dsn: admin ? h.adminUrl : h.appUrl, queries });
  const output = execFileSync("intelligence/.venv/bin/python", ["__tests__/support/pg_parameterized.py"], {
    input,
    encoding: "utf8",
    stdio: ["pipe", "pipe", "pipe"],
  });
  const result = JSON.parse(output);
  h.contextAfter = result.context_after;
  h.lastColumns = result.columns ?? [];
  if (result.error) throw new Error(result.error);
  return result.results;
}

/** Arrays that also expose `row[column]`, so both builder (positional) and raw execute (named) consumers work. */
function withNames(rows: unknown[][], columns: string[]) {
  return rows.map((row) => Object.assign([...row], Object.fromEntries(columns.map((name, i) => [name, row[i]]))));
}

export async function createBridgeDb(h: Harness) {
  const { drizzle } = await import("drizzle-orm/pg-proxy");
  const schema = await import("@/lib/db-schema");
  const db = drizzle(
    async (sql, params) => {
      if (!h.queued) {
        const rows = runBatch(h, [{ sql, params }])[0];
        return { rows: withNames(rows, h.lastColumns[0] ?? []) };
      }
      return new Promise((resolve, reject) => h.queued!.push({ sql, params, resolve, reject }));
    },
    { schema }
  );
  // neon-http resolves a raw execute() to { rows }, pg-proxy to the bare rows: make the bridge look like neon-http.
  const execute = db.execute.bind(db) as (query: unknown) => PromiseLike<unknown[]>;
  Object.assign(db, {
    execute: (query: unknown) => ({
      then: (resolve: (v: unknown) => unknown, reject?: (e: unknown) => unknown) =>
        execute(query).then((rows) => ({ rows }), reject).then(resolve, reject),
    }),
    batch: async (queries: PromiseLike<unknown>[]) => {
      const pending: NonNullable<Harness["queued"]> = [];
      h.queued = pending;
      const promises = queries.map((q) => Promise.resolve(q));
      await new Promise((resolve) => setImmediate(resolve));
      h.queued = null;
      try {
        const results = runBatch(h, pending);
        pending.forEach((q, i) => q.resolve({ rows: withNames(results[i], h.lastColumns[i] ?? []) }));
      } catch (error) {
        pending.forEach((q) => q.reject(error as Error));
      }
      return Promise.all(promises);
    },
  });
  return db;
}
