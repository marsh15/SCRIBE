import { and, eq, gte, lte, sql } from "drizzle-orm";
import { db } from "@/lib/db-config";
import { usageEvents, subscriptions } from "@/lib/db-schema";
import { PLAN_CATALOG, type PlanCode } from "@/lib/billing/plans";
import { calculateOverageInr } from "@/lib/billing/rating";

export type UsageMetric =
  | "model_input_tokens"
  | "model_output_tokens"
  | "embedding_input_tokens"
  | "storage_gb_day";

export async function recordUsageEvent(input: {
  userId: string;
  metric: UsageMetric;
  quantity: number;
  unit: "tokens" | "gb_day";
  sourceType?: string;
  sourceId?: string;
  isEstimated?: boolean;
  occurredAt?: Date;
}) {
  await db.insert(usageEvents).values({
    userId: input.userId,
    metric: input.metric,
    quantity: Math.max(0, Math.round(input.quantity)),
    unit: input.unit,
    sourceType: input.sourceType,
    sourceId: input.sourceId,
    isEstimated: input.isEstimated ?? false,
    occurredAt: input.occurredAt ?? new Date(),
  });
}

function getCycleStartEnd(now = new Date()) {
  const start = new Date(now.getFullYear(), now.getMonth(), 1, 0, 0, 0, 0);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0, 23, 59, 59, 999);
  return { start, end };
}

async function getCurrentPlanCode(userId: string): Promise<PlanCode> {
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

export async function getUsageSummary(userId: string) {
  const { start, end } = getCycleStartEnd();

  const rows = await db
    .select({
      metric: usageEvents.metric,
      total: sql<number>`sum(${usageEvents.quantity})::int`,
    })
    .from(usageEvents)
    .where(
      and(
        eq(usageEvents.userId, userId),
        gte(usageEvents.occurredAt, start),
        lte(usageEvents.occurredAt, end)
      )
    )
    .groupBy(usageEvents.metric);

  const usage = {
    modelInputTokens: 0,
    modelOutputTokens: 0,
    embeddingTokens: 0,
    storageGbDay: 0,
  };

  for (const row of rows) {
    if (row.metric === "model_input_tokens") usage.modelInputTokens = Number(row.total ?? 0);
    if (row.metric === "model_output_tokens") usage.modelOutputTokens = Number(row.total ?? 0);
    if (row.metric === "embedding_input_tokens") usage.embeddingTokens = Number(row.total ?? 0);
    if (row.metric === "storage_gb_day") usage.storageGbDay = Number(row.total ?? 0) / 1000;
  }

  const planCode = await getCurrentPlanCode(userId);
  const plan = PLAN_CATALOG[planCode];
  const included = {
    modelInputTokens: plan.limits.includedModelInputTokens,
    modelOutputTokens: plan.limits.includedModelOutputTokens,
    embeddingTokens: plan.limits.includedEmbeddingTokens,
    storageGb: plan.limits.storageGb,
  };

  const projectedOverageInr = calculateOverageInr(usage, included);

  return {
    period: { start, end },
    planCode,
    included,
    usage,
    projectedOverageInr,
    allowOverage: plan.limits.allowOverage,
  };
}
