import { NextResponse } from "next/server";
import { db } from "@/lib/db-config";
import { sql } from "drizzle-orm";

export const runtime = "nodejs";

export async function GET() {
  try {
    // Check DB connectivity with a lightweight query
    await db.execute(sql`SELECT 1`);

    return NextResponse.json({
      ok: true,
      db: "connected",
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    console.error("[Health] DB check failed:", error);
    return NextResponse.json(
      {
        ok: false,
        db: "unreachable",
        error: error instanceof Error ? error.message : "Unknown error",
        timestamp: new Date().toISOString(),
      },
      { status: 503 }
    );
  }
}
