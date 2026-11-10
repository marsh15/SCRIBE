import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { config } from "dotenv";
import * as schema from "./db-schema";

config({ path: ".env.local", quiet: true });

const connectionString = process.env.NEON_DATABASE_URL;
// Runtime role vs owner role. `db` is the RUNTIME connection: once it connects as `scribe_app` (drizzle/0013) every table
// read or write needs the workspace context from `scoped()` (lib/scoped.ts). `adminDb`/`adminSql` is the OWNER connection
// for the few operations that cannot run inside one workspace: cross-workspace admin dashboards, the legacy-engine job
// claim and runtime schema repair (the allowed importers are listed, with reasons, in __tests__/security/db-boundary.test.ts). Until the credentials are split it is the
// same database URL, so nothing changes today.
const adminConnectionString = process.env.NEON_ADMIN_DATABASE_URL || connectionString;

function missingDatabaseConfiguration(): never {
  throw new Error(
    "NEON_DATABASE_URL is not configured. Add it to .env.local or the deployment environment before using database-backed features.",
  );
}

const createSql = (url: string) => neon(url);
type NeonClient = ReturnType<typeof createSql>;

const unavailableSql = new Proxy((() => undefined) as unknown as NeonClient, {
  apply: missingDatabaseConfiguration,
  get: missingDatabaseConfiguration,
});

const createDatabase = (client: NeonClient) => drizzle(client, { schema });
type Database = ReturnType<typeof createDatabase>;

const unavailableDatabase = new Proxy({} as Database, {
  get: missingDatabaseConfiguration,
});

// Use neon-http: Prevents WebSocket connection limit hanging in Next.js dev server.
// It executes queries multiplexed over HTTP and natively supports db.batch() for atomicity.
const runtimeSql = connectionString ? createSql(connectionString) : unavailableSql;
export const db = connectionString ? createDatabase(runtimeSql) : unavailableDatabase;

/** Owner-role connection. Only the modules in the allow-list of __tests__/security/db-boundary.test.ts may import it. */
export const adminSql = adminConnectionString ? createSql(adminConnectionString) : unavailableSql;
export const adminDb = adminConnectionString ? createDatabase(adminSql) : unavailableDatabase;

// Export a dummy pool if code still explicitly imports it, or we can just remove pool imports.
// (We should remove `pool` imports from the codebase instead).
