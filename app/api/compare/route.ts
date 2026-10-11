import { NextResponse } from "next/server";
import { isNotAuthenticatedError } from "@/lib/auth";
import { internalError } from "@/lib/api-errors";
import { callIntelligence } from "@/lib/intelligence-client";
import { resolveWorkspace } from "@/lib/workspace";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const TIMEOUT_MS = 55_000;
const NO_STORE = { "cache-control": "no-store" } as const;

const err = (status: number, code: string, message: string, extra: Record<string, unknown> = {}) =>
  NextResponse.json({ error: { code, message, ...extra } }, { status, headers: NO_STORE });

/** Only these fields are forwarded. A workspace or user in the body, query or headers is never read (INV-15). */
function pickBody(raw: unknown): { ok: true; body: Record<string, unknown> } | { ok: false } {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { ok: false };
  const r = raw as Record<string, unknown>;
  const body: Record<string, unknown> = { query: r.query, profiles: r.profiles };
  if (r.limit !== undefined) body.limit = r.limit;
  const sourceIds = r.sourceIds ?? r.source_ids;
  if (sourceIds !== undefined) body.source_ids = sourceIds;
  return { ok: true, body };
}

/** FastAPI's 422 body echoes the offending input; keep only where and why. */
function validationIssues(detail: unknown): Array<{ field: string; message: string }> {
  if (!Array.isArray(detail)) return [];
  return detail.slice(0, 10).map((d) => {
    const item = (d ?? {}) as { loc?: unknown[]; msg?: unknown };
    const field = (item.loc ?? []).filter((p) => p !== "body").join(".") || "body";
    return { field, message: typeof item.msg === "string" ? item.msg.slice(0, 200) : "invalid" };
  });
}

/**
 * Compare 2-5 retrieval profiles for one query (T29). Workspace and user come from the Clerk session only; the Python
 * service runs every profile in one REPEATABLE READ snapshot. Python 404 (unknown profile) and 422 (validation) pass
 * through with a sanitized body; any other failure is a 502/503 that carries no service details.
 */
export async function POST(req: Request) {
  try {
    const { userId, workspaceId } = await resolveWorkspace();
    let raw: unknown;
    try {
      raw = await req.json();
    } catch {
      return err(400, "INVALID_JSON", "The request body must be JSON.");
    }
    const picked = pickBody(raw);
    if (!picked.ok) return err(400, "INVALID_JSON", "The request body must be a JSON object.");

    let res: Response;
    try {
      res = await callIntelligence(
        "/compare",
        { sub: userId, wid: workspaceId, scope: "compare" },
        {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(picked.body),
          signal: AbortSignal.timeout(TIMEOUT_MS),
        },
      );
    } catch {
      return err(502, "COMPARE_UNAVAILABLE", "The comparison service could not be reached. Please retry.");
    }

    if (res.status === 404) {
      const j = (await res.json().catch(() => ({}))) as { unknown?: unknown; profiles?: unknown };
      return err(404, "UNKNOWN_PROFILE", "One or more profiles are not available for comparison.", {
        unknown: Array.isArray(j.unknown) ? j.unknown.filter((x) => typeof x === "string") : [],
        profiles: Array.isArray(j.profiles) ? j.profiles.filter((x) => typeof x === "string") : [],
      });
    }
    if (res.status === 422) {
      const j = (await res.json().catch(() => ({}))) as { detail?: unknown };
      return err(422, "INVALID_REQUEST", "Choose 2 to 5 distinct profiles and enter a query.", {
        issues: validationIssues(j.detail),
      });
    }
    if (res.status === 503) {
      return err(503, "RETRIEVAL_FAILED", "Search is unavailable right now. Please retry shortly.");
    }
    if (!res.ok) {
      return err(502, "COMPARE_UNAVAILABLE", "The comparison could not be completed. Please retry.");
    }
    try {
      return NextResponse.json(await res.json(), { headers: NO_STORE });
    } catch {
      return err(502, "COMPARE_UNAVAILABLE", "The comparison returned an unreadable response.");
    }
  } catch (error) {
    if (isNotAuthenticatedError(error)) return err(401, "UNAUTHENTICATED", "Sign in required.");
    return internalError("compare", error);
  }
}
