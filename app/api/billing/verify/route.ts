import { NextResponse } from "next/server";
import { z } from "zod";
import crypto from "node:crypto";
import { getUserId } from "@/lib/auth";
import { setUserSubscription } from "@/lib/billing/store";
import type { PlanCode, BillingGateway } from "@/lib/billing/plans";

const verifySchema = z.object({
    razorpay_order_id: z.string(),
    razorpay_payment_id: z.string(),
    razorpay_signature: z.string(),
    planCode: z.enum(["pro", "team"]),
    gateway: z.literal("razorpay"),
});

export async function POST(req: Request) {
    try {
        const userId = await getUserId();
        const parsed = verifySchema.safeParse(await req.json());

        if (!parsed.success) {
            return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
        }

        const {
            razorpay_order_id,
            razorpay_payment_id,
            razorpay_signature,
            planCode,
            gateway,
        } = parsed.data;

        // Verify payment signature per Razorpay docs:
        // generated_signature = hmac_sha256(order_id + "|" + payment_id, key_secret)
        const keySecret = process.env.RAZORPAY_KEY_SECRET;
        if (!keySecret) {
            return NextResponse.json({ error: "Server misconfiguration" }, { status: 500 });
        }

        const expectedSignature = crypto
            .createHmac("sha256", keySecret)
            .update(`${razorpay_order_id}|${razorpay_payment_id}`)
            .digest("hex");

        const isValid = (() => {
            try {
                return crypto.timingSafeEqual(
                    Buffer.from(razorpay_signature),
                    Buffer.from(expectedSignature)
                );
            } catch {
                return false;
            }
        })();

        if (!isValid) {
            return NextResponse.json({ error: "Payment verification failed" }, { status: 400 });
        }

        // Payment verified — activate the subscription
        await setUserSubscription({
            userId,
            planCode: planCode as PlanCode,
            gateway: gateway as BillingGateway,
            providerSubscriptionId: razorpay_payment_id,
            status: "active",
            currentPeriodStart: new Date(),
            currentPeriodEnd: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000), // +30 days
            cancelAtPeriodEnd: false,
        });

        return NextResponse.json({ ok: true, verified: true });
    } catch (error) {
        console.error("Payment verify error:", error);
        return NextResponse.json({ error: "Verification failed" }, { status: 500 });
    }
}
