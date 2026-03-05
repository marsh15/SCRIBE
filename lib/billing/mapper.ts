import { getGatewayPriceId, PLAN_CATALOG, type BillingGateway, type PlanCode, type CurrencyCode } from "@/lib/billing/plans";

export function planFromProviderId(input: {
  gateway: BillingGateway;
  providerPriceOrPlanId?: string | null;
}): PlanCode {
  const target = input.providerPriceOrPlanId ?? "";
  if (!target) return "free";

  const currencies: CurrencyCode[] = ["INR", "USD"];
  const planCodes = Object.keys(PLAN_CATALOG) as PlanCode[];

  for (const code of planCodes) {
    for (const currency of currencies) {
      const id = getGatewayPriceId(code, input.gateway, currency);
      if (id && id === target) return code;
    }
  }

  return "free";
}
