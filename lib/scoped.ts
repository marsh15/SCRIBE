import { sql as rawSql } from "drizzle-orm";
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
 */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type BatchItem = Parameters<typeof db.batch>[0][number];

export interface ScopedDb {
  readonly workspaceId: string;
  /** Run the queries atomically with `app.workspace_id` set. Returns the results of `queries` only. */
  batch<T extends BatchItem[]>(queries: [...T]): Promise<unknown[]>;
}

function contextStatement(workspaceId: string) {
  return db.execute(rawSql`select set_config('app.workspace_id', ${workspaceId}, true)`);
}

export function scoped(workspaceId: string): ScopedDb {
  if (!UUID_RE.test(workspaceId)) throw new Error("scoped(): workspaceId must be a UUID");
  const ctx = workspaceId;
  return {
    workspaceId: ctx,
    async batch(queries) {
      const results = await db.batch([contextStatement(ctx), ...queries] as unknown as [BatchItem, ...BatchItem[]]);
      return (results as unknown[]).slice(1);
    },
  };
}
