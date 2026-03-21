import { Pool, neonConfig } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";
import { config } from "dotenv";
import * as schema from "./db-schema";
import ws from "ws";

config({ path: ".env.local" });

// Use WebSocket for connection pooling (required for neon-serverless in Node.js)
neonConfig.webSocketConstructor = ws;

function createDb(pool: Pool) {
  return drizzle(pool, { schema });
}

const globalForDb = globalThis as typeof globalThis & {
  __scribePool?: Pool;
  __scribeDb?: ReturnType<typeof createDb>;
};

const connectionString = process.env.NEON_DATABASE_URL;

if (!connectionString) {
  throw new Error("NEON_DATABASE_URL is not configured");
}

export const pool =
  globalForDb.__scribePool ??
  new Pool({
    connectionString,
  });

export const db = globalForDb.__scribeDb ?? createDb(pool);

if (process.env.NODE_ENV !== "production") {
  globalForDb.__scribePool = pool;
  globalForDb.__scribeDb = db;
}
