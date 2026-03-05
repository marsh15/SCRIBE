export type PlanCode = "free" | "pro" | "team";
export type BillingGateway = "stripe" | "razorpay";
export type CurrencyCode = "INR" | "USD";

export interface PlanDefinition {
  code: PlanCode;
  name: string;
  monthlyPrice: Record<CurrencyCode, number>;
  description: string;
  limits: {
    maxFileSizeMb: number;
    storageGb: number;
    includedModelInputTokens: number;
    includedModelOutputTokens: number;
    includedEmbeddingTokens: number;
    allowOverage: boolean;
  };
}

/** Client-safe plan catalog — no process.env references */
export const PLAN_CATALOG: Record<PlanCode, PlanDefinition> = {
  free: {
    code: "free",
    name: "Free",
    monthlyPrice: { INR: 0, USD: 0 },
    description: "Try Scribe with strict usage limits and no overage.",
    limits: {
      maxFileSizeMb: 25,
      storageGb: 0.1,
      includedModelInputTokens: 120_000,
      includedModelOutputTokens: 30_000,
      includedEmbeddingTokens: 100_000,
      allowOverage: false,
    },
  },
  pro: {
    code: "pro",
    name: "Pro",
    monthlyPrice: { INR: 499, USD: 9 },
    description: "For solo professionals with metered overage support.",
    limits: {
      maxFileSizeMb: 100,
      storageGb: 5,
      includedModelInputTokens: 1_000_000,
      includedModelOutputTokens: 300_000,
      includedEmbeddingTokens: 700_000,
      allowOverage: true,
    },
  },
  team: {
    code: "team",
    name: "Team",
    monthlyPrice: { INR: 1999, USD: 29 },
    description: "For teams that need higher quotas and faster throughput.",
    limits: {
      maxFileSizeMb: 100,
      storageGb: 25,
      includedModelInputTokens: 5_000_000,
      includedModelOutputTokens: 1_500_000,
      includedEmbeddingTokens: 3_500_000,
      allowOverage: true,
    },
  },
};

export const DEFAULT_CURRENCY: CurrencyCode = "INR";

export const OVERAGE_INR_RATES = {
  modelInputPer1k: 0.06,
  modelOutputPer1k: 0.5,
  embeddingPer1k: 0.04,
  storagePerGbMonth: 20,
};

export function getPlan(planCode: PlanCode) {
  return PLAN_CATALOG[planCode];
}

/**
 * Server-only: resolve gateway price IDs from environment variables.
 * This must NEVER be imported from "use client" components.
 */
export function getGatewayPriceId(
  planCode: PlanCode,
  gateway: BillingGateway,
  currency: CurrencyCode
): string {
  const lookup: Record<string, string | undefined> = {
    "pro_stripe_INR": process.env.STRIPE_PRICE_PRO_INR,
    "pro_stripe_USD": process.env.STRIPE_PRICE_PRO_USD,
    "team_stripe_INR": process.env.STRIPE_PRICE_TEAM_INR,
    "team_stripe_USD": process.env.STRIPE_PRICE_TEAM_USD,
    "pro_razorpay_INR": process.env.RAZORPAY_PLAN_PRO_INR,
    "team_razorpay_INR": process.env.RAZORPAY_PLAN_TEAM_INR,
  };

  return lookup[`${planCode}_${gateway}_${currency}`] ?? "";
}
