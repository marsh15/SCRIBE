import { NextResponse } from "next/server";
import { processQueuedIngestionJobs } from "@/lib/ingestion/worker";

function authorized(req: Request) {
  const expected = process.env.INTERNAL_CRON_SECRET;
  if (!expected) return true;
  return req.headers.get("x-internal-secret") === expected;
}

export async function POST(req: Request) {
  try {
    if (!authorized(req)) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const url = new URL(req.url);
    const limit = Number(url.searchParams.get("limit") ?? 2);

    const result = await processQueuedIngestionJobs(Math.max(1, Math.min(limit, 20)));
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    console.error("Ingestion worker run error:", error);
    return NextResponse.json({ error: "Failed to process ingestion queue" }, { status: 500 });
  }
}
