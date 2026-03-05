import { NextResponse } from "next/server";
import { verifyRazorpaySignature } from "@/lib/billing/webhooks";
import {
  markPaymentEventIfNew,
  markPaymentEventProcessed,
  setUserSubscription,
} from "@/lib/billing/store";
import { planFromProviderId } from "@/lib/billing/mapper";

function toDate(ts: unknown) {
  if (typeof ts !== "number") return undefined;
  return new Date(ts * 1000);
}

export async function POST(req: Request) {
  try {
    const rawBody = await req.text();
    const signature = req.headers.get("x-razorpay-signature");

    if (!verifyRazorpaySignature(rawBody, signature)) {
      return NextResponse.json({ error: "Invalid Razorpay signature" }, { status: 401 });
    }

    const event = JSON.parse(rawBody) as {
      event: string;
      created_at?: number;
      payload?: {
        subscription?: { entity?: Record<string, unknown> };
        payment?: { entity?: Record<string, unknown> };
      };
    };

    const eventId = String(
      event.payload?.subscription?.entity?.id ??
        event.payload?.payment?.entity?.id ??
        `${event.event}-${event.created_at ?? Date.now()}`
    );

    const marker = await markPaymentEventIfNew({
      gateway: "razorpay",
      eventId,
      eventType: event.event,
      payload: event,
    });

    if (!marker.isNew) {
      return NextResponse.json({ ok: true, deduped: true });
    }

    const sub = event.payload?.subscription?.entity ?? {};
    const notes = (sub.notes as Record<string, string> | undefined) ?? {};
    const userId = notes.userId;

    if (userId && (event.event.includes("subscription") || event.event.includes("payment"))) {
      const providerPlanId = (sub.plan_id as string | undefined) ?? undefined;
      const status = String(sub.status ?? (event.event.includes("failed") ? "past_due" : "active"));

      await setUserSubscription({
        userId,
        planCode: planFromProviderId({
          gateway: "razorpay",
          providerPriceOrPlanId: providerPlanId,
        }),
        gateway: "razorpay",
        providerSubscriptionId: (sub.id as string | undefined) ?? undefined,
        status,
        currentPeriodStart: toDate(sub.current_start),
        currentPeriodEnd: toDate(sub.current_end),
        cancelAtPeriodEnd: Boolean(sub.cancel_at_cycle_end ?? false),
      });
    }

    await markPaymentEventProcessed(marker.id);

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error("Razorpay webhook error:", error);
    return NextResponse.json({ error: "Webhook processing failed" }, { status: 500 });
  }
}
