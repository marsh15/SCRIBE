import { and, eq } from "drizzle-orm";
import { db } from "@/lib/db-config";
import {
  billingCustomers,
  paymentEvents,
  subscriptions,
  type InsertSubscription,
} from "@/lib/db-schema";
import type { BillingGateway, PlanCode } from "@/lib/billing/plans";

export async function getOrCreateBillingCustomer(userId: string, gateway: BillingGateway) {
  const existing = await db.query.billingCustomers.findFirst({
    where: eq(billingCustomers.userId, userId),
  });

  if (existing) return existing;

  const [created] = await db
    .insert(billingCustomers)
    .values({
      userId,
      defaultGateway: gateway,
      currency: "INR",
    })
    .returning();

  return created;
}

export async function upsertGatewayCustomerId(input: {
  userId: string;
  gateway: BillingGateway;
  customerId: string;
}) {
  const current = await getOrCreateBillingCustomer(input.userId, input.gateway);

  await db
    .update(billingCustomers)
    .set({
      stripeCustomerId:
        input.gateway === "stripe" ? input.customerId : current.stripeCustomerId,
      razorpayCustomerId:
        input.gateway === "razorpay" ? input.customerId : current.razorpayCustomerId,
      defaultGateway: input.gateway,
      updatedAt: new Date(),
    })
    .where(eq(billingCustomers.id, current.id));
}

export async function setUserSubscription(input: {
  userId: string;
  planCode: PlanCode;
  gateway: BillingGateway;
  providerSubscriptionId?: string;
  status: string;
  currentPeriodStart?: Date;
  currentPeriodEnd?: Date;
  cancelAtPeriodEnd?: boolean;
}) {
  const existing = await db.query.subscriptions.findFirst({
    where: and(eq(subscriptions.userId, input.userId), eq(subscriptions.gateway, input.gateway)),
    orderBy: (table, { desc }) => [desc(table.updatedAt)],
  });

  const payload: InsertSubscription = {
    userId: input.userId,
    planCode: input.planCode,
    gateway: input.gateway,
    providerSubscriptionId: input.providerSubscriptionId,
    status: input.status,
    currentPeriodStart: input.currentPeriodStart,
    currentPeriodEnd: input.currentPeriodEnd,
    cancelAtPeriodEnd: input.cancelAtPeriodEnd ?? false,
    updatedAt: new Date(),
  };

  if (!existing) {
    await db.insert(subscriptions).values(payload);
    return;
  }

  await db.update(subscriptions).set(payload).where(eq(subscriptions.id, existing.id));
}

export async function markPaymentEventIfNew(input: {
  gateway: BillingGateway;
  eventId: string;
  eventType?: string;
  payload?: unknown;
}) {
  const existing = await db.query.paymentEvents.findFirst({
    where: and(
      eq(paymentEvents.gateway, input.gateway),
      eq(paymentEvents.eventId, input.eventId)
    ),
  });

  if (existing) return { isNew: false, id: existing.id };

  const [created] = await db
    .insert(paymentEvents)
    .values({
      gateway: input.gateway,
      eventId: input.eventId,
      eventType: input.eventType,
      payload: (input.payload ?? null) as never,
      processed: false,
    })
    .returning();

  return { isNew: true, id: created.id };
}

export async function markPaymentEventProcessed(eventDbId: number) {
  await db
    .update(paymentEvents)
    .set({ processed: true, processedAt: new Date() })
    .where(eq(paymentEvents.id, eventDbId));
}
