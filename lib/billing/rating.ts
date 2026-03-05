import { OVERAGE_INR_RATES } from "@/lib/billing/plans";

export interface UsageTotals {
  modelInputTokens: number;
  modelOutputTokens: number;
  embeddingTokens: number;
  storageGbDay: number;
}

export interface IncludedTotals {
  modelInputTokens: number;
  modelOutputTokens: number;
  embeddingTokens: number;
  storageGb: number;
}

export function calculateOverageInr(
  usage: UsageTotals,
  included: IncludedTotals
): number {
  const inputOver = Math.max(0, usage.modelInputTokens - included.modelInputTokens);
  const outputOver = Math.max(0, usage.modelOutputTokens - included.modelOutputTokens);
  const embedOver = Math.max(0, usage.embeddingTokens - included.embeddingTokens);

  const storageGbMonth = usage.storageGbDay / 30;
  const storageOver = Math.max(0, storageGbMonth - included.storageGb);

  const total =
    (inputOver / 1000) * OVERAGE_INR_RATES.modelInputPer1k +
    (outputOver / 1000) * OVERAGE_INR_RATES.modelOutputPer1k +
    (embedOver / 1000) * OVERAGE_INR_RATES.embeddingPer1k +
    storageOver * OVERAGE_INR_RATES.storagePerGbMonth;

  return Number(total.toFixed(2));
}
