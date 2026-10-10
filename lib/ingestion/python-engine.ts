import { callIntelligence } from "@/lib/intelligence-client";

/** The Python indexer rejected the Source (4xx/422): retrying the same bytes will not help. */
export class PermanentIngestError extends Error {
  constructor(
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "PermanentIngestError";
  }
}

export interface PythonIngestResult {
  status: string;
  generationId: string | null;
  chunks: number;
  embedded: number;
  warnings: string[];
}

function errorDetail(body: unknown): { message: string | null; code: string | null } {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return { message: detail, code: null };
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; error_code?: unknown };
    return {
      message: typeof d.message === "string" ? d.message : null,
      code: typeof d.error_code === "string" ? d.error_code : null,
    };
  }
  return { message: null, code: null };
}

/**
 * POST /api/py/ingest/source for one queued Source (scope `ingest`, workspace from the token).
 * 4xx (except 408/429) is permanent; 5xx, 408/429 and network failures are retryable.
 */
export async function ingestSourceViaPython(source: {
  id: number;
  userId: string;
  workspaceId: string;
}): Promise<PythonIngestResult> {
  let response: Response;
  try {
    response = await callIntelligence(
      "/ingest/source",
      { sub: source.userId, wid: source.workspaceId, scope: "ingest" },
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ source_id: source.id }),
      },
    );
  } catch (error) {
    const reason = error instanceof Error ? error.message : "unknown error";
    throw new Error(`Intelligence service unreachable: ${reason}`);
  }

  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const { message, code } = errorDetail(body);
    const text = message ?? `Intelligence service returned HTTP ${response.status}`;
    const retryable = response.status >= 500 || response.status === 408 || response.status === 429;
    if (retryable) throw new Error(text);
    throw new PermanentIngestError(text, code);
  }
  const ok = body as {
    status?: string;
    generation_id?: string | null;
    chunks?: number;
    embedded?: number;
    warnings?: string[];
  };
  return {
    status: ok.status ?? "ACTIVE",
    generationId: ok.generation_id ?? null,
    chunks: ok.chunks ?? 0,
    embedded: ok.embedded ?? 0,
    warnings: ok.warnings ?? [],
  };
}
