import { SignJWT } from "jose";

import { requireEnv } from "@/lib/env";

export const INTERNAL_TOKEN_AUDIENCE = "scribe-intel";
export const INTERNAL_TOKEN_TTL_SECONDS = 60;

export type InternalTokenInput = {
  sub: string;
  wid: string;
  scope: string;
};

/** Mint a 60 s HS256 token for the Python intelligence service (D5). */
export async function mintInternalToken(
  input: InternalTokenInput,
  secret: string = requireEnv("INTERNAL_SERVICE_SECRET"),
): Promise<string> {
  const issuedAt = Math.floor(Date.now() / 1000);
  return new SignJWT({ wid: input.wid, scope: input.scope })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(input.sub)
    .setAudience(INTERNAL_TOKEN_AUDIENCE)
    .setIssuedAt(issuedAt)
    .setExpirationTime(issuedAt + INTERNAL_TOKEN_TTL_SECONDS)
    .sign(new TextEncoder().encode(secret));
}
