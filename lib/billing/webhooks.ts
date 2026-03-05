import crypto from "node:crypto";

export function verifyStripeSignature(rawBody: string, stripeSignatureHeader: string | null) {
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!secret) throw new Error("Missing STRIPE_WEBHOOK_SECRET");
  if (!stripeSignatureHeader) return false;

  const pairs = stripeSignatureHeader.split(",").map((chunk) => chunk.split("="));
  const timestamp = pairs.find(([k]) => k === "t")?.[1];
  const signatures = pairs.filter(([k]) => k === "v1").map(([, v]) => v);

  if (!timestamp || signatures.length === 0) return false;

  const signedPayload = `${timestamp}.${rawBody}`;
  const expected = crypto
    .createHmac("sha256", secret)
    .update(signedPayload)
    .digest("hex");

  return signatures.some((sig) => {
    try {
      return crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected));
    } catch {
      return false;
    }
  });
}

export function verifyRazorpaySignature(rawBody: string, signatureHeader: string | null) {
  const secret = process.env.RAZORPAY_WEBHOOK_SECRET;
  if (!secret) throw new Error("Missing RAZORPAY_WEBHOOK_SECRET");
  if (!signatureHeader) return false;

  const expected = crypto.createHmac("sha256", secret).update(rawBody).digest("hex");

  try {
    return crypto.timingSafeEqual(Buffer.from(signatureHeader), Buffer.from(expected));
  } catch {
    return false;
  }
}
