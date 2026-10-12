import { after, NextResponse } from "next/server";
import { hasCronSecret } from "@/lib/cron-auth";
import { fireTick, MAX_TICK_HOPS, runStep } from "@/lib/ingestion/tick";

export const maxDuration = 300;

/**
 * One tick = one leased Python step (up to ~240 s). Answers 202 at once; the step runs in after() and, while
 * claimable work remains, re-fires this route (Python caps live leases at 3, so the chain fans out to <= 3).
 */
async function tick(req: Request) {
  if (!hasCronSecret(req)) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  const hop = Math.max(0, Number(new URL(req.url).searchParams.get("hop") ?? 0) || 0);
  if (hop >= MAX_TICK_HOPS) return NextResponse.json({ ok: true, accepted: false, reason: "hop_limit" });

  after(async () => {
    try {
      const step = await runStep();
      if (step.more) await fireTick(hop);
    } catch (error) {
      console.error("[IngestTick] step failed", error instanceof Error ? error.message : "error");
    }
  });
  return NextResponse.json({ ok: true, accepted: true, hop }, { status: 202 });
}

export const GET = tick;
export const POST = tick;
