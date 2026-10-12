import { callIntelligence } from "@/lib/intelligence-client";
import type { RagToolResult } from "@/lib/rag-types";
import { splitClaims, type Claim } from "@/lib/evidence/claims";

/**
 * Client/server shapes for post-stream claim verification (T26).
 * INV-9: "Verified" is only ever what the service returned as VERIFIED; any other path (timeout, error, unreachable,
 * malformed answer) ends in `unavailable` or UNVERIFIED, never a verified badge.
 */
export type ClaimStatus = "VERIFIED" | "PARTIAL" | "UNSUPPORTED" | "UNCITED" | "UNVERIFIED" | "INVALID_CITATION";

export type VerifiedClaim = { text: string; status: ClaimStatus; labels: string[] };

export type VerificationData =
  | { state: "verifying" }
  | { state: "done"; claims: VerifiedClaim[] }
  | { state: "unavailable" };

export const VERIFICATION_PART = "data-verification";
export const VERIFICATION_PART_ID = "verification";

export const STATUS_META: Record<ClaimStatus, { label: string; icon: string; description: string }> = {
  VERIFIED: { label: "Verified", icon: "✓", description: "The cited passage states this claim" },
  PARTIAL: { label: "Partly supported", icon: "◐", description: "The cited passage supports only part of this claim" },
  UNSUPPORTED: { label: "Unsupported", icon: "✕", description: "The cited passage does not support this claim" },
  UNCITED: { label: "Uncited", icon: "○", description: "No source was cited for this claim" },
  UNVERIFIED: { label: "Unverified", icon: "?", description: "This claim could not be checked" },
  INVALID_CITATION: { label: "Invalid citation", icon: "!", description: "The citation does not match stored evidence" },
};

const STATUSES = new Set<string>(Object.keys(STATUS_META));
const VERIFY_TIMEOUT_MS = 30_000;

export function bundlesFromResults(results: readonly RagToolResult[]) {
  const seen = new Set<string>();
  const bundles: Array<{ bundle_id: string; items: Array<{ label: string; item_id: string; source_version_id?: string }> }> = [];
  for (const r of results) {
    const ev = r.evidence;
    if (!ev?.bundleId || seen.has(ev.bundleId)) continue;
    seen.add(ev.bundleId);
    bundles.push({
      bundle_id: ev.bundleId,
      items: ev.items.map((i) => ({
        label: i.label,
        item_id: i.id,
        ...(i.sourceVersionId ? { source_version_id: i.sourceVersionId } : {}),
      })),
    });
  }
  return bundles;
}

export function parseVerifiedClaims(raw: unknown): VerifiedClaim[] | null {
  if (!Array.isArray(raw)) return null;
  const out: VerifiedClaim[] = [];
  for (const c of raw) {
    if (!c || typeof c !== "object" || typeof (c as { text?: unknown }).text !== "string") return null;
    const { text, status, labels } = c as { text: string; status?: unknown; labels?: unknown };
    out.push({
      text,
      status: typeof status === "string" && STATUSES.has(status) ? (status as ClaimStatus) : "UNVERIFIED",
      labels: Array.isArray(labels) ? labels.filter((l): l is string => typeof l === "string") : [],
    });
  }
  return out;
}

/** Verify the finished answer of a Python-engine turn. Never throws; every failure is `unavailable`. */
export async function requestVerification(
  input: { userId: string; workspaceId: string; messageId?: string; text: string; results: readonly RagToolResult[] },
  call: typeof callIntelligence = callIntelligence,
): Promise<VerificationData> {
  const claims = splitClaims(input.text);
  if (claims.length === 0) return { state: "done", claims: [] };
  try {
    const res = await call(
      "/verify",
      { sub: input.userId, wid: input.workspaceId, scope: "verify" },
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          ...(input.messageId ? { message_id: input.messageId } : {}),
          bundles: bundlesFromResults(input.results),
          claims: claims.map((c) => ({ index: c.index, text: c.text })),
        }),
        signal: AbortSignal.timeout(VERIFY_TIMEOUT_MS),
      },
    );
    if (!res.ok) return { state: "unavailable" };
    const parsed = parseVerifiedClaims(((await res.json()) as { claims?: unknown })?.claims);
    // A different number of claims back than sent means we cannot line them up: do not guess.
    if (!parsed || parsed.length !== claims.length) return { state: "unavailable" };
    return { state: "done", claims: parsed };
  } catch {
    return { state: "unavailable" };
  }
}

/** Read stored verifications for finished messages (best effort: absent on any failure). */
export async function readStoredVerifications(
  input: { userId: string; workspaceId: string; messageIds: string[] },
  call: typeof callIntelligence = callIntelligence,
): Promise<Record<string, VerificationData>> {
  if (input.messageIds.length === 0) return {};
  try {
    const res = await call(
      "/verify/read",
      { sub: input.userId, wid: input.workspaceId, scope: "verify" },
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ message_ids: input.messageIds }),
        signal: AbortSignal.timeout(10_000),
      },
    );
    if (!res.ok) return {};
    const data = (await res.json()) as { messages?: Record<string, unknown> };
    const out: Record<string, VerificationData> = {};
    for (const [id, rows] of Object.entries(data.messages ?? {})) {
      const claims = parseVerifiedClaims(rows);
      if (claims) out[id] = { state: "done", claims };
    }
    return out;
  } catch {
    return {};
  }
}

/**
 * Line the service's claims up with the claims split from the displayed text. Matching is by claim text, in order, so a
 * re-split of a stored answer finds its stored rows; a claim with no stored row simply has no badge.
 */
export function matchClaims(claims: readonly Claim[], verified: readonly VerifiedClaim[]): Map<number, VerifiedClaim> {
  const out = new Map<number, VerifiedClaim>();
  const used = new Set<number>();
  for (const c of claims) {
    const at = verified.findIndex((v, i) => !used.has(i) && v.text === c.text);
    if (at >= 0) {
      used.add(at);
      out.set(c.index, verified[at]);
    }
  }
  return out;
}

const CLAIM_HREF = "#claim-";

/** Insert a `[status](#claim-N)` marker after each claim; CitedMarkdown turns it into a badge. */
export function annotateClaims(text: string, claims: readonly Claim[], byIndex: ReadonlyMap<number, VerifiedClaim>): string {
  let out = text;
  for (const c of [...claims].reverse()) {
    if (!byIndex.has(c.index)) continue;
    out = `${out.slice(0, c.end)} [status](${CLAIM_HREF}${c.index})${out.slice(c.end)}`;
  }
  return out;
}

export function parseClaimHref(href: string | undefined): number | null {
  if (!href?.startsWith(CLAIM_HREF)) return null;
  const n = Number(href.slice(CLAIM_HREF.length));
  return Number.isInteger(n) ? n : null;
}

/** generating -> verifying -> final, for one assistant message. */
export type VerifyPhase = "generating" | "verifying" | "final" | "unavailable" | "none";

export function verificationPhase(streaming: boolean, data: VerificationData | undefined): VerifyPhase {
  if (data?.state === "done") return "final";
  if (data?.state === "unavailable") return "unavailable";
  if (data?.state === "verifying") return "verifying";
  return streaming ? "generating" : "none";
}

export function verificationFromParts(parts: readonly unknown[] | undefined): VerificationData | undefined {
  for (const p of [...(parts ?? [])].reverse()) {
    const part = p as { type?: string; data?: VerificationData };
    if (part?.type === VERIFICATION_PART && part.data && typeof part.data === "object") return part.data;
  }
  return undefined;
}
