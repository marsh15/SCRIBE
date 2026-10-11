import { env } from "@/lib/env";
import { callIntelligence } from "@/lib/intelligence-client";
import {
  RAG_TOOL_RESULT_TYPE,
  type RagToolChunk,
  type RagToolResult,
} from "@/lib/rag-types";

export type RetrievalEngine = "python" | "legacy";

export const SEARCH_UNAVAILABLE_MESSAGE =
  "Search unavailable. Your documents could not be searched right now, so no answer can be given. Please retry shortly.";

const REQUEST_TIMEOUT_MS = 25_000;

export function getRetrievalEngine(value: string | undefined = env.RETRIEVAL_ENGINE): RetrievalEngine {
  return value === "python" ? "python" : "legacy";
}

/** D16: injected memory cannot be cited, so Supermemory is off whenever evidence mode is on. */
export function shouldUseSupermemory(engine: RetrievalEngine, supermemoryFlag: boolean) {
  return engine !== "python" && supermemoryFlag;
}

type PyCandidate = {
  rank: number;
  chunk_id: string;
  doc_name: string;
  heading_text?: string | null;
  content: string;
  source_id: number;
  dense_rank?: number | null;
  lexical_rank?: number | null;
  fusion_rank?: number | null;
  rerank_rank?: number | null;
  rerank_score?: number | null;
  fusion_score?: number | null;
};

type PyContextEntry = {
  chunk_id: string;
  text: string;
  heading_text?: string | null;
  doc_name: string;
  source_id: number;
};

export type PyRetrieveResponse = {
  profile: string;
  trace_id?: string | null;
  degraded?: string[];
  candidates?: PyCandidate[];
  stage_ms?: Record<string, number>;
  counts?: Record<string, number>;
  meta?: Record<string, unknown>;
  config_hash?: string | null;
  context?: { entries?: PyContextEntry[] };
};

export function unavailableResult(query: string, reason?: string): RagToolResult {
  return {
    type: RAG_TOOL_RESULT_TYPE,
    engine: "python",
    query,
    status: "unavailable",
    message: SEARCH_UNAVAILABLE_MESSAGE,
    context: `SEARCH_UNAVAILABLE${reason ? ` (${reason})` : ""}. No evidence was retrieved. Do not answer the question. Reply only with: "${SEARCH_UNAVAILABLE_MESSAGE}"`,
    timings: { embeddingMs: 0, retrievalMs: 0, totalMs: 0 },
    topK: 0,
    threshold: 0,
    chunks: [],
  };
}

/** Evidence text with source and section labels; degraded results are labelled (INV-16). */
export function formatPythonContext(
  entries: PyContextEntry[],
  degraded: string[],
): string {
  const body = entries
    .map((e, i) => {
      const section = e.heading_text?.trim() || "(no section)";
      return `[Evidence ${i + 1}] Source: [${e.doc_name}](/files/${e.source_id}) | Section: ${section}\nContent: ${e.text}`;
    })
    .join("\n\n---\n\n");
  return degraded.length > 0
    ? `NOTE: results are degraded (${degraded.join(", ")}); say so in the answer.\n\n${body}`
    : body;
}

function preview(text: string, max = 360) {
  const n = text.replace(/\s+/g, " ").trim();
  return n.length <= max ? n : `${n.slice(0, max - 3).trim()}...`;
}

export function buildPythonToolResult(query: string, res: PyRetrieveResponse, totalMs: number): RagToolResult {
  const degraded = res.degraded ?? [];
  const candidates = res.candidates ?? [];
  const entries: PyContextEntry[] =
    res.context?.entries ??
    candidates.map((c) => ({
      chunk_id: c.chunk_id,
      text: c.content,
      heading_text: c.heading_text,
      doc_name: c.doc_name,
      source_id: c.source_id,
    }));
  const selectedIds = new Set(entries.map((e) => e.chunk_id));
  const chunks: RagToolChunk[] = candidates.map((c, i) => ({
    rank: c.rank ?? i + 1,
    documentId: 0,
    fileId: c.source_id,
    fileName: c.doc_name,
    fileType: "",
    chunkIndex: 0,
    similarity: c.rerank_score ?? c.fusion_score ?? 0,
    metadata: null,
    contentPreview: preview(c.content),
    content: c.content,
    section: c.heading_text ?? null,
    selected: selectedIds.has(c.chunk_id),
    stageRanks: {
      dense: c.dense_rank ?? null,
      lexical: c.lexical_rank ?? null,
      fused: c.fusion_rank ?? null,
      reranked: c.rerank_rank ?? null,
    },
  }));
  const empty = entries.length === 0;
  const emptyMessage =
    "No relevant information found in the knowledge base. The knowledge base may be empty — please upload documents first.";
  const stageMs = res.stage_ms ?? {};
  return {
    type: RAG_TOOL_RESULT_TYPE,
    engine: "python",
    traceId: res.trace_id ?? undefined,
    query,
    status: empty ? "empty" : "retrieved",
    message: empty ? emptyMessage : undefined,
    context: empty ? emptyMessage : formatPythonContext(entries, degraded),
    timings: {
      embeddingMs: stageMs.embed ?? 0,
      retrievalMs: stageMs.dense ?? 0,
      totalMs: stageMs.request_total ?? totalMs,
    },
    topK: chunks.length,
    threshold: 0,
    chunks,
    retrieval: {
      engine: "python",
      profile: res.profile,
      configHash: res.config_hash ?? null,
      degraded,
      stageMs,
      counts: res.counts ?? {},
    },
  };
}

/**
 * Call the Python retrieval service. Workspace and user come from the server-side
 * session, never client input. Any failure becomes an "unavailable" result: no
 * fallback to legacy and no answer without evidence (B4, INV-16).
 */
export async function retrieveViaPython(input: {
  query: string;
  userId: string;
  workspaceId: string;
  limit?: number;
}): Promise<RagToolResult> {
  const start = Date.now();
  try {
    const response = await callIntelligence(
      "/retrieve",
      { sub: input.userId, wid: input.workspaceId, scope: "retrieve" },
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: input.query, limit: input.limit ?? 10 }),
        signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      },
    );
    if (!response.ok) {
      return unavailableResult(input.query, `http ${response.status}`);
    }
    const data = (await response.json()) as PyRetrieveResponse;
    if (!data || typeof data !== "object" || !Array.isArray(data.candidates)) {
      return unavailableResult(input.query, "malformed response");
    }
    return buildPythonToolResult(input.query, data, Date.now() - start);
  } catch (error) {
    console.error("[RAG] Python retrieval failed:", error instanceof Error ? error.name : "error");
    return unavailableResult(input.query, "service error");
  }
}
