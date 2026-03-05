import { db } from "@/lib/db-config";
import { billingCustomers } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { upsertGatewayCustomerId } from "@/lib/billing/store";

type StripeCheckoutInput = {
  userId: string;
  email?: string | null;
  planName: string;
  amountInMinorUnit: number;
  currency: string;
  priceId?: string;
  successUrl: string;
  cancelUrl: string;
  metadata: Record<string, string>;
};

const STRIPE_API = "https://api.stripe.com/v1";

function stripeSecret() {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) throw new Error("Missing STRIPE_SECRET_KEY");
  return key;
}

function toFormBody(data: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(data)) {
    if (v === undefined || v === null || v === "") continue;
    params.append(k, String(v));
  }
  return params;
}

async function ensureStripeCustomer(userId: string, email?: string | null) {
  const existing = await db.query.billingCustomers.findFirst({
    where: eq(billingCustomers.userId, userId),
  });

  if (existing?.stripeCustomerId) return existing.stripeCustomerId;

  const body = toFormBody({ email: email ?? undefined });
  body.append("metadata[user_id]", userId);

  const res = await fetch(`${STRIPE_API}/customers`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${stripeSecret()}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Stripe customer create failed: ${text}`);
  }

  const json = (await res.json()) as { id: string };
  await upsertGatewayCustomerId({ userId, gateway: "stripe", customerId: json.id });
  return json.id;
}

export async function createStripeCheckoutSession(input: StripeCheckoutInput) {
  const customerId = await ensureStripeCustomer(input.userId, input.email);
  const body = new URLSearchParams();
  body.append("mode", "subscription");
  body.append("customer", customerId);
  body.append("success_url", input.successUrl);
  body.append("cancel_url", input.cancelUrl);

  for (const [key, value] of Object.entries(input.metadata)) {
    body.append(`metadata[${key}]`, value);
  }

  if (input.priceId) {
    body.append("line_items[0][price]", input.priceId);
  } else {
    body.append("line_items[0][price_data][currency]", input.currency.toLowerCase());
    body.append("line_items[0][price_data][recurring][interval]", "month");
    body.append("line_items[0][price_data][product_data][name]", `Scribe ${input.planName}`);
    body.append("line_items[0][price_data][unit_amount]", String(input.amountInMinorUnit));
  }
  body.append("line_items[0][quantity]", "1");

  const res = await fetch(`${STRIPE_API}/checkout/sessions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${stripeSecret()}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Stripe checkout failed: ${text}`);
  }

  const json = (await res.json()) as { url?: string; id: string };
  return {
    url: json.url,
    sessionId: json.id,
  };
}

export async function createStripePortalSession(userId: string, returnUrl: string) {
  const customer = await db.query.billingCustomers.findFirst({
    where: eq(billingCustomers.userId, userId),
  });

  if (!customer?.stripeCustomerId) {
    throw new Error("No Stripe customer found for user");
  }

  const body = toFormBody({ customer: customer.stripeCustomerId, return_url: returnUrl });

  const res = await fetch(`${STRIPE_API}/billing_portal/sessions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${stripeSecret()}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Stripe portal failed: ${text}`);
  }

  const json = (await res.json()) as { url: string };
  return json.url;
}
