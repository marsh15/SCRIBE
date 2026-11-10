import { sql as rawSql } from "drizzle-orm";
import type { BatchItem, BatchResponse } from "drizzle-orm/batch";
import { db } from "@/lib/db-config";

/**
 * Workspace-scoped execution for Postgres RLS (INV-1, drizzle/0013).
 *
 * neon-http sends each statement as its own request, so `set_config(..., true)` (transaction-local) only has an
 * effect inside a batch, which Neon runs as one transaction. `scoped(id).batch([...])` therefore prepends the
 * set_config statement to the batch and strips its result. Never call set_config with `is_local = false`: with
 * pooled connections the context would leak to the next request.
 *
 * The workspace id must come from `resolveWorkspace()` (server-derived), never from client input.
 *
 * `scoped(id).sql` is the same thing for one raw statement: a template tag that returns the rows, so a query written as
 * ``sql`...` `` becomes ``scoped(workspaceId).sql`...` `` and still runs with the context set.
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type RowTag = <T = Record<string, unknown>>(strings: TemplateStringsArray, ...values: unknown[]) => Promise<T[]>;

export interface ScopedDb {
  readonly workspaceId: string;
  /** Run the queries atomically with `app.workspace_id` set. Returns the results of `queries` only. */
  batch<T extends [BatchItem<"pg">, ...BatchItem<"pg">[]]>(queries: T): Promise<BatchResponse<T>>;
  /** One raw statement (it may be a multi-CTE statement) with the workspace context set; resolves to its rows. */
  sql: RowTag;
}

function contextStatement(workspaceId: string) {
  return db.execute(rawSql`select set_config('app.workspace_id', ${workspaceId}, true)`);
}

export function scoped(workspaceId: string): ScopedDb {
  if (!UUID_RE.test(workspaceId)) throw new Error("scoped(): workspaceId must be a UUID");
  const ctx = workspaceId;
  async function batch<T extends [BatchItem<"pg">, ...BatchItem<"pg">[]]>(queries: T): Promise<BatchResponse<T>> {
    const results = await db.batch([contextStatement(ctx), ...queries] as unknown as [BatchItem<"pg">, ...BatchItem<"pg">[]]);
    return (results as unknown[]).slice(1) as BatchResponse<T>;
  }
  const rows: RowTag = async <T>(strings: TemplateStringsArray, ...values: unknown[]) => {
    const [result] = await batch([db.execute(rawSql(strings, ...values))]);
    return (result as { rows: T[] }).rows;
  };
  return { workspaceId: ctx, batch, sql: rows };
}

/**
 * Text of a database error including its causes. Drizzle wraps driver errors in a "Failed query: ..." error and keeps
 * the Postgres message (for example the codes raised by scribe_reserve_source_version) on `cause`.
 */
export function dbErrorText(error: unknown): string {
  const parts: string[] = [];
  for (let e: unknown = error, depth = 0; e instanceof Error && depth < 5; e = (e as { cause?: unknown }).cause, depth += 1) {
    parts.push(e.message);
  }
  return parts.join(" | ");
}
