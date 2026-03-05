import { NextResponse } from "next/server";
import { verifyStripeSignature } from "@/lib/billing/webhooks";
import {
  markPaymentEventIfNew,
  markPaymentEventProcessed,
  setUserSubscription,
} from "@/lib/billing/store";
import { planFromProviderId } from "@/lib/billing/mapper";

function parseUnixTs(value: unknown) {
  if (typeof value !== "number") return undefined;
  return new Date(value * 1000);
}

export async function POST(req: Request) {
  try {
    const rawBody = await req.text();
    const signature = req.headers.get("stripe-signature");

    if (!verifyStripeSignature(rawBody, signature)) {
      return NextResponse.json({ error: "Invalid Stripe signature" }, { status: 401 });
    }

    const event = JSON.parse(rawBody) as {
      id: string;
      type: string;
      data?: { object?: Record<string, unknown> };
    };

    const marker = await markPaymentEventIfNew({
      gateway: "stripe",
      eventId: event.id,
      eventType: event.type,
      payload: event,
    });

    if (!marker.isNew) {
      return NextResponse.json({ ok: true, deduped: true });
    }

    const object = event.data?.object ?? {};

    if (event.type === "checkout.session.completed") {
      const metadata = (object.metadata as Record<string, string> | undefined) ?? {};
      const userId = metadata.userId;
      const planCode = (metadata.planCode as "free" | "pro" | "team" | undefined) ?? "free";
      if (userId) {
        await setUserSubscription({
          userId,
          planCode,
          gateway: "stripe",
          providerSubscriptionId: (object.subscription as string) ?? undefined,
          status: "active",
        });
      }
    }

    if (event.type === "customer.subscription.updated" || event.type === "customer.subscription.created") {
      const metadata = (object.metadata as Record<string, string> | undefined) ?? {};
      const userId = metadata.userId;
      const providerPriceId =
        ((object.items as { data?: Array<{ price?: { id?: string } }> } | undefined)?.data?.[0]
          ?.price?.id as string | undefined) ?? undefined;

      if (userId) {
        await setUserSubscription({
          userId,
          planCode: planFromProviderId({
            gateway: "stripe",
            providerPriceOrPlanId: providerPriceId,
          }),
          gateway: "stripe",
          providerSubscriptionId: (object.id as string) ?? undefined,
          status: String(object.status ?? "active"),
          currentPeriodStart: parseUnixTs(object.current_period_start),
          currentPeriodEnd: parseUnixTs(object.current_period_end),
          cancelAtPeriodEnd: Boolean(object.cancel_at_period_end ?? false),
        });
      }
    }

    if (event.type === "customer.subscription.deleted") {
      const metadata = (object.metadata as Record<string, string> | undefined) ?? {};
      const userId = metadata.userId;
      if (userId) {
        await setUserSubscription({
          userId,
          planCode: "free",
          gateway: "stripe",
          providerSubscriptionId: (object.id as string) ?? undefined,
          status: "canceled",
          currentPeriodStart: parseUnixTs(object.current_period_start),
          currentPeriodEnd: parseUnixTs(object.current_period_end),
          cancelAtPeriodEnd: true,
        });
      }
    }

    await markPaymentEventProcessed(marker.id);

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Stripe webhook error:", error);
    return NextResponse.json({ error: "Webhook processing failed" }, { status: 500 });
  }
}
