import { and, eq } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { subscriptions } from "@/lib/db-schema";
import { PLAN_CATALOG, type PlanCode } from "@/lib/billing/plans";

export const ABSOLUTE_MAX_FILE_SIZE_MB = 100;

export async function getUserPlanCode(userId: string): Promise<PlanCode> {
  const active = await db.query.subscriptions.findFirst({
    where: and(eq(subscriptions.userId, userId), eq(subscriptions.status, "active")),
    orderBy: (table, { desc }) => [desc(table.updatedAt)],
  });

  if (!active) return "free";
  if (active.planCode === "pro" || active.planCode === "team" || active.planCode === "free") {
    return active.planCode;
  }

  return "free";
}

export async function getUserMaxUploadBytes(userId: string) {
  const planCode = await getUserPlanCode(userId);
  const byPlan = PLAN_CATALOG[planCode].limits.maxFileSizeMb;
  const capped = Math.min(byPlan, ABSOLUTE_MAX_FILE_SIZE_MB);
  return {
    planCode,
    maxBytes: capped * 1024 * 1024,
    maxMb: capped,
  };
}
