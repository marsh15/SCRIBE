export function resolveBillingPortalUrl(
  manageBillingUrl = process.env.RAZORPAY_MANAGE_BILLING_URL
) {
  const trimmed = manageBillingUrl?.trim();
  return trimmed ? trimmed : null;
}

export function isBillingPortalAvailable(
  manageBillingUrl = process.env.RAZORPAY_MANAGE_BILLING_URL
) {
  return resolveBillingPortalUrl(manageBillingUrl) !== null;
}
