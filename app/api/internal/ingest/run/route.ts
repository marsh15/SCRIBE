import { NextResponse } from "next/server";
import { processQueuedIngestionJobs } from "@/lib/ingestion/worker";
import crypto from "node:crypto";
import { env } from "@/lib/env";
import { internalError } from "@/lib/api-errors";

function hasCronSecret(req: Request) {
  const expected = env.INTERNAL_CRON_SECRET;
  if (!expected) return false;
  const supplied = req.headers.get("x-internal-secret");
  if (!supplied) return false;
  const actualBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

export const maxDuration = 60;

export async function POST(req: Request) {
  try {
    if (!hasCronSecret(req)) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const url = new URL(req.url);
    const limit = Number(url.searchParams.get("limit") ?? 2);

    const result = await processQueuedIngestionJobs(Math.max(1, Math.min(limit, 20)));
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    return internalError("ingestion-worker", error);
  }
}
