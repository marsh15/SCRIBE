import { NextResponse } from "next/server";
import { runQueuedSourceIntake } from "@/lib/ingestion/source-intake";
import { hasCronSecret } from "@/lib/cron-auth";
import { internalError } from "@/lib/api-errors";

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
