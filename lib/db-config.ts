import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { config } from "dotenv";
import * as schema from "./db-schema";

config({ path: ".env.local" });

const connectionString = process.env.NEON_DATABASE_URL;

if (!connectionString) {
  throw new Error("NEON_DATABASE_URL is not configured");
}

// Use neon-http: Prevents WebSocket connection limit hanging in Next.js dev server.
// It executes queries multiplexed over HTTP and natively supports db.batch() for atomicity.
export const sql = neon(connectionString);
export const db = drizzle(sql, { schema });

// Export a dummy pool if code still explicitly imports it, or we can just remove pool imports.
// (We should remove `pool` imports from the codebase instead).

