import { NextResponse } from "next/server";
import { getUserId } from "@/lib/auth";
import { getUsageSummary } from "@/lib/billing/usage";
import { PLAN_CATALOG } from "@/lib/billing/plans";

export async function GET() {
  try {
    const userId = await getUserId();
    const summary = await getUsageSummary(userId);

    return NextResponse.json({
      ok: true,
      ...summary,
      plan: PLAN_CATALOG[summary.planCode],
    });
  } catch (error) {
    console.error("Usage fetch error:", error);
    return NextResponse.json({ error: "Failed to fetch usage" }, { status: 500 });
  }
}
