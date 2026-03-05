import { NextResponse } from "next/server";
import { z } from "zod";
import { getUserId } from "@/lib/auth";
import { flags } from "@/lib/flags";
import { createStripePortalSession } from "@/lib/billing/gateways/stripe";
import { createRazorpayPortalLink } from "@/lib/billing/gateways/razorpay";

const schema = z.object({
  gateway: z.enum(["stripe", "razorpay"]),
  returnUrl: z.string().url().optional(),
});

export async function POST(req: Request) {
  try {
    if (!flags.billingEnabled) {
      return NextResponse.json({ error: "Billing is currently disabled" }, { status: 503 });
    }

    const userId = await getUserId();
    const parsed = schema.safeParse(await req.json());

    if (!parsed.success) {
      return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
    }

    const requestUrl = new URL(req.url);
    const appOrigin = process.env.NEXT_PUBLIC_APP_URL ?? requestUrl.origin;
    const returnUrl = parsed.data.returnUrl ?? `${appOrigin}/settings/billing`;

    const portalUrl =
      parsed.data.gateway === "stripe"
        ? await createStripePortalSession(userId, returnUrl)
        : await createRazorpayPortalLink();

    return NextResponse.json({ ok: true, url: portalUrl });
  } catch (error) {
    console.error("Billing portal error:", error);
    return NextResponse.json({ error: "Failed to create billing portal link" }, { status: 500 });
  }
}
