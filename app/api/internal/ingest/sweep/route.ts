import { after, NextResponse } from "next/server";
import { hasCronSecret } from "@/lib/cron-auth";
import { fireTick, runSweep } from "@/lib/ingestion/tick";
import { internalError } from "@/lib/api-errors";

export const maxDuration = 60;

/** Daily cron (Hobby): requeue expired leases, then kick a tick if anything is claimable (incl. due retries). */
async function sweep(req: Request) {
  if (!hasCronSecret(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const result = await runSweep();
    if (result.more) after(async () => void (await fireTick()));
    return NextResponse.json({ ok: true, ...result });
  } catch (error) {
    return internalError("ingestion-sweep", error);
  }
}

export const GET = sweep;
export const POST = sweep;
