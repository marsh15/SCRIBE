import { PLAN_CATALOG, type BillingGateway, type PlanCode } from "@/lib/billing/plans";

export function planFromProviderId(input: {
  gateway: BillingGateway;
  providerPriceOrPlanId?: string | null;
}): PlanCode {
  const target = input.providerPriceOrPlanId ?? "";
  if (!target) return "free";

  const allPlans = Object.values(PLAN_CATALOG);
  for (const plan of allPlans) {
    const ids = Object.values(plan.gatewayPriceIds[input.gateway]).filter(Boolean);
    if (ids.includes(target)) return plan.code;
  }

  return "free";
}
