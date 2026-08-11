import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { config } from "dotenv";
import * as schema from "./db-schema";

config({ path: ".env.local", quiet: true });

const connectionString = process.env.NEON_DATABASE_URL;

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
export const sql = connectionString ? createSql(connectionString) : unavailableSql;
export const db = connectionString ? createDatabase(sql) : unavailableDatabase;

// Export a dummy pool if code still explicitly imports it, or we can just remove pool imports.
// (We should remove `pool` imports from the codebase instead).
