import { NextResponse } from "next/server";
import { z } from "zod";
import { getUserId, isNotAuthenticatedError } from "@/lib/auth";
import { flags } from "@/lib/flags";
import { createRazorpayPortalLink } from "@/lib/billing/gateways/razorpay";
import { isBillingPortalAvailable } from "@/lib/billing/portal";

const schema = z.object({
  gateway: z.enum(["razorpay"]),
  returnUrl: z.string().url().optional(),
});

export async function POST(req: Request) {
  try {
    if (!flags.billingEnabled) {
      return NextResponse.json({ error: "Billing is currently disabled" }, { status: 503 });
    }

    await getUserId();
    if (!isBillingPortalAvailable()) {
      return NextResponse.json(
        { error: "Billing portal is not configured for this deployment." },
        { status: 503 }
      );
    }
    const parsed = schema.safeParse(await req.json());

    if (!parsed.success) {
      return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });
    }

    const portalUrl = await createRazorpayPortalLink();

    return NextResponse.json({ ok: true, url: portalUrl });
  } catch (error) {
    if (isNotAuthenticatedError(error)) {
      return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
    }
    console.error("Billing portal error:", error);
    return NextResponse.json({ error: "Failed to create billing portal link" }, { status: 500 });
  }
}
