import { after, NextResponse } from "next/server";
import { hasCronSecret } from "@/lib/cron-auth";
import { fireTick, runPurge, runSweep } from "@/lib/ingestion/tick";
import { internalError } from "@/lib/api-errors";

export const maxDuration = 60;

/** Daily cron (Hobby): requeue expired leases, purge soft-deleted Sources, then kick a tick if anything is claimable (incl. due retries). */
async function sweep(req: Request) {
  if (!hasCronSecret(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  try {
    const result = await runSweep();
    if (result.more) after(async () => void (await fireTick()));
    // T20: purge soft-deleted Sources past the grace period. A purge failure must not fail the lease sweep; it is
    // recorded on the Source (purge_error_code) and reported here.
    let purge: Awaited<ReturnType<typeof runPurge>> | { error: string };
    try {
      purge = await runPurge();
    } catch (error) {
      console.error("[ingestion-sweep] purge failed", error instanceof Error ? error.message : "unknown");
      purge = { error: "purge_failed" };
    }
    return NextResponse.json({ ok: true, ...result, purge });
  } catch (error) {
    return internalError("ingestion-sweep", error);
  }
}

export const GET = sweep;
export const POST = sweep;
