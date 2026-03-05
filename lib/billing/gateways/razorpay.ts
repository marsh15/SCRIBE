import { db } from "@/lib/db-config";
import { billingCustomers } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { upsertGatewayCustomerId } from "@/lib/billing/store";

type RazorpayCheckoutInput = {
  userId: string;
  email?: string | null;
  name?: string | null;
  planName: string;
  amountInMinorUnit: number;
  currency: string;
  planId?: string;
  successUrl: string;
  cancelUrl: string;
  metadata: Record<string, string>;
};

const RAZORPAY_API = "https://api.razorpay.com/v1";

function razorpayCreds() {
  const keyId = process.env.RAZORPAY_KEY_ID;
  const keySecret = process.env.RAZORPAY_KEY_SECRET;
  if (!keyId || !keySecret) throw new Error("Missing Razorpay credentials");
  return { keyId, keySecret };
}

function authHeader() {
  const { keyId, keySecret } = razorpayCreds();
  const token = Buffer.from(`${keyId}:${keySecret}`).toString("base64");
  return `Basic ${token}`;
}

async function ensureRazorpayCustomer(input: {
  userId: string;
  email?: string | null;
  name?: string | null;
}) {
  const existing = await db.query.billingCustomers.findFirst({
    where: eq(billingCustomers.userId, input.userId),
  });

  if (existing?.razorpayCustomerId) return existing.razorpayCustomerId;

  const res = await fetch(`${RAZORPAY_API}/customers`, {
    method: "POST",
    headers: {
      Authorization: authHeader(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name: input.name ?? "Scribe User",
      email: input.email,
      notes: { userId: input.userId },
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Razorpay customer create failed: ${text}`);
  }

  const json = (await res.json()) as { id: string };
  await upsertGatewayCustomerId({
    userId: input.userId,
    gateway: "razorpay",
    customerId: json.id,
  });

  return json.id;
}

export async function createRazorpayCheckout(input: RazorpayCheckoutInput) {
  const customerId = await ensureRazorpayCustomer({
    userId: input.userId,
    email: input.email,
    name: input.name,
  });

  // Prefer subscription plan id when configured; fallback to payment link.
  if (input.planId) {
    const subRes = await fetch(`${RAZORPAY_API}/subscriptions`, {
      method: "POST",
      headers: {
        Authorization: authHeader(),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        plan_id: input.planId,
        customer_notify: 1,
        total_count: 12,
        customer_id: customerId,
        notes: input.metadata,
      }),
    });

    if (subRes.ok) {
      const json = (await subRes.json()) as { id: string; short_url?: string };
      return {
        url: json.short_url ?? input.successUrl,
        sessionId: json.id,
      };
    }
  }

  const paymentRes = await fetch(`${RAZORPAY_API}/payment_links`, {
    method: "POST",
    headers: {
      Authorization: authHeader(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      amount: input.amountInMinorUnit,
      currency: input.currency,
      accept_partial: false,
      description: `Scribe ${input.planName} monthly plan`,
      customer: {
        name: input.name ?? "Scribe User",
        email: input.email,
      },
      notify: { sms: false, email: true },
      callback_url: input.successUrl,
      callback_method: "get",
      notes: {
        ...input.metadata,
        cancelUrl: input.cancelUrl,
      },
    }),
  });

  if (!paymentRes.ok) {
    const text = await paymentRes.text();
    throw new Error(`Razorpay checkout failed: ${text}`);
  }

  const json = (await paymentRes.json()) as { short_url: string; id: string };
  return {
    url: json.short_url,
    sessionId: json.id,
  };
}

export async function createRazorpayPortalLink() {
  // Razorpay does not provide a direct customer billing portal equivalent.
  return process.env.RAZORPAY_MANAGE_BILLING_URL ?? process.env.NEXT_PUBLIC_APP_URL ?? "/pricing";
}
