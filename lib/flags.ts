function parseFlag(value: string | undefined, defaultValue = false) {
  if (value === undefined) return defaultValue;
  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

export const flags = {
  billingEnabled: parseFlag(process.env.FEATURE_BILLING_ENABLED, true),
  asyncIngestionEnabled: parseFlag(process.env.FEATURE_ASYNC_INGESTION_ENABLED, true),
  publicLandingEnabled: parseFlag(process.env.FEATURE_PUBLIC_LANDING_ENABLED, true),
};
