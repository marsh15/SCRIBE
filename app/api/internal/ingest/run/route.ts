import { NextResponse } from "next/server";
import { runQueuedSourceIntake } from "@/lib/ingestion/source-intake";
import crypto from "node:crypto";
import { env } from "@/lib/env";
import { internalError } from "@/lib/api-errors";

function hasCronSecret(req: Request) {
  const expected = env.CRON_SECRET;
  if (!expected) return false;
  const authorization = req.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) return false;
  const supplied = authorization.slice("Bearer ".length);
  const actualBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}

export const maxDuration = 300;

async function run(req: Request) {
  try {
    if (!hasCronSecret(req)) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const url = new URL(req.url);
    const limit = Number(url.searchParams.get("limit") ?? 1);

    const result = await runQueuedSourceIntake(Math.max(1, Math.min(limit, 5)));
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    return internalError("ingestion-worker", error);
  }
}

export const GET = run;
export const POST = run;
