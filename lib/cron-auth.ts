import crypto from "node:crypto";
import { env } from "@/lib/env";

/** True when the request carries `Authorization: Bearer ${CRON_SECRET}` (Vercel cron and our own tick re-fires). */
export function hasCronSecret(req: Request): boolean {
  const expected = env.CRON_SECRET;
  if (!expected) return false;
  const authorization = req.headers.get("authorization");
  if (!authorization?.startsWith("Bearer ")) return false;
  const actualBuffer = Buffer.from(authorization.slice("Bearer ".length));
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && crypto.timingSafeEqual(actualBuffer, expectedBuffer);
}
