import { db } from "@/lib/db-config";
import { billingCustomers } from "@/lib/db-schema";
import { eq } from "drizzle-orm";
import { upsertGatewayCustomerId } from "@/lib/billing/store";

type RazorpayOrderInput = {
  userId: string;
  email?: string | null;
  name?: string | null;
  planName: string;
  amountInMinorUnit: number;
  currency: string;
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

/**
 * Creates a Razorpay Order using the Standard Checkout flow.
 * Returns the order details needed by the client to open the Razorpay modal.
 */
export async function createRazorpayOrder(input: RazorpayOrderInput) {
  await ensureRazorpayCustomer({
    userId: input.userId,
    email: input.email,
    name: input.name,
  });

  const { keyId } = razorpayCreds();

  const receipt = `scribe_${input.metadata.planCode}_${Date.now()}`;

  const res = await fetch(`${RAZORPAY_API}/orders`, {
    method: "POST",
    headers: {
      Authorization: authHeader(),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      amount: input.amountInMinorUnit,
      currency: input.currency,
      receipt,
      notes: input.metadata,
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`Razorpay order creation failed: ${text}`);
  }

  const order = (await res.json()) as {
    id: string;
    amount: number;
    currency: string;
    receipt: string;
  };

  return {
    orderId: order.id,
    amount: order.amount,
    currency: order.currency,
    keyId,
    receipt: order.receipt,
    name: "Scribe",
    description: `Scribe ${input.planName} monthly plan`,
    prefill: {
      name: input.name ?? undefined,
      email: input.email ?? undefined,
    },
  };
}

export async function createRazorpayPortalLink() {
  return process.env.RAZORPAY_MANAGE_BILLING_URL ?? process.env.NEXT_PUBLIC_APP_URL ?? "/pricing";
}
