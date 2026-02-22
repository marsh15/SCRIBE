import { drizzle } from "drizzle-orm/neon-http";

import { neon } from "@neondatabase/serverless";
import { config } from "dotenv";
import * as schema from './db-schema';

config({ path: ".env" })

const sql = neon(process.env.NEON_DATABASE_URL!);
export const db = drizzle(sql, { schema });
