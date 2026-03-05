import { NextResponse } from "next/server";
import { z } from "zod";
import { currentUser } from "@clerk/nextjs/server";
import { getUserId } from "@/lib/auth";
import { flags } from "@/lib/flags";
import {
  PLAN_CATALOG,
  type CurrencyCode,
  getGatewayPriceId,
} from "@/lib/billing/plans";
import { createStripeCheckoutSession } from "@/lib/billing/gateways/stripe";
import { createRazorpayOrder } from "@/lib/billing/gateways/razorpay";

const requestSchema = z.object({
  planCode: z.enum(["free", "pro", "team"]),
  gateway: z.enum(["stripe", "razorpay"]),
  currency: z.enum(["INR", "USD"]).optional().default("INR"),
  successUrl: z.string().url(),
  cancelUrl: z.string().url(),
});

function toMinorUnit(currency: CurrencyCode, amount: number) {
  if (currency === "INR" || currency === "USD") return Math.round(amount * 100);
  return Math.round(amount * 100);
}

export async function POST(req: Request) {
  try {
    if (!flags.billingEnabled) {
      return NextResponse.json({ error: "Billing is currently disabled" }, { status: 503 });
    }

    const userId = await getUserId();
    const user = await currentUser();
    const parsed = requestSchema.safeParse(await req.json());

    if (!parsed.success) {
      return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
    }

    const { planCode, gateway, currency, successUrl, cancelUrl } = parsed.data;

    if (planCode === "free") {
      return NextResponse.json(
        { error: "Free plan does not require checkout" },
        { status: 400 }
      );
    }

    const plan = PLAN_CATALOG[planCode];
    const price = plan.monthlyPrice[currency];
    const priceId = getGatewayPriceId(planCode, gateway, currency);

    const email = user?.primaryEmailAddress?.emailAddress ?? null;

    const metadata = {
      userId,
      planCode,
      gateway,
      currency,
    };

    if (gateway === "stripe") {
      const session = await createStripeCheckoutSession({
        userId,
        email,
        planName: plan.name,
        amountInMinorUnit: toMinorUnit(currency, price),
        currency,
        priceId,
        successUrl,
        cancelUrl,
        metadata,
      });

      return NextResponse.json({
        ok: true,
        gateway: "stripe",
        checkoutUrl: session.url,
        sessionId: session.sessionId,
      });
    }

    // Razorpay Standard Checkout — returns order details for inline modal
    const order = await createRazorpayOrder({
      userId,
      email,
      name: user?.fullName,
      planName: plan.name,
      amountInMinorUnit: toMinorUnit(currency, price),
      currency,
      metadata,
    });

    return NextResponse.json({
      ok: true,
      gateway: "razorpay",
      order,
    });
  } catch (error) {
    console.error("Billing checkout error:", error);
    return NextResponse.json({ error: "Failed to create checkout session" }, { status: 500 });
  }
}
