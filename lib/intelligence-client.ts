import { env } from "@/lib/env";
import { mintInternalToken, type InternalTokenInput } from "@/lib/internal-token";

export function baseUrl(): string {
  if (env.INTELLIGENCE_BASE_URL) return env.INTELLIGENCE_BASE_URL.replace(/\/$/, "");
  if (env.VERCEL_URL) return `https://${env.VERCEL_URL}`;
  return "http://localhost:3000";
}

/**
 * Call the Python service at `${base}/api/py${path}`. Server-side only: the
 * token never reaches the browser. On protected previews the Vercel
 * automation-bypass header is added when VERCEL_AUTOMATION_BYPASS_SECRET is set.
 */
export async function callIntelligence(
  path: string,
  token: InternalTokenInput,
  init: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("authorization", `Bearer ${await mintInternalToken(token)}`);
  if (env.VERCEL_AUTOMATION_BYPASS_SECRET) {
    headers.set("x-vercel-protection-bypass", env.VERCEL_AUTOMATION_BYPASS_SECRET);
  }
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return fetch(`${baseUrl()}/api/py${suffix}`, { ...init, headers, cache: "no-store" });
}
