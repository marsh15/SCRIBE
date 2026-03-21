import { Pool, neonConfig } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";
import { config } from "dotenv";
import * as schema from './db-schema';
import ws from "ws";

config({ path: ".env.local" });

// Use WebSocket for connection pooling (required for neon-serverless in Node.js)
neonConfig.webSocketConstructor = ws;

const pool = new Pool({ connectionString: process.env.NEON_DATABASE_URL! });
export const db = drizzle(pool, { schema });
