import type { SearchDocumentsResult } from "@/lib/search";

export const RAG_TOOL_RESULT_TYPE = "rag_search_result";

export type RagToolChunk = {
  rank: number;
  documentId: number;
  fileId: number;
  fileName: string;
  fileType: string;
  chunkIndex: number;
  similarity: number;
  metadata: unknown;
  contentPreview: string;
  content: string;
  /** Python engine only: section heading and per-stage ranks. */
  section?: string | null;
  stageRanks?: StageRanks;
  selected?: boolean;
};

export type StageRanks = {
  dense: number | null;
  lexical: number | null;
  fused: number | null;
  reranked: number | null;
};

/** Python retrieval details (T16). Absent on legacy results and on old messages. */
export type RagRetrievalMeta = {
  engine: "python";
  profile: string;
  configHash?: string | null;
  degraded: string[];
  stageMs: Record<string, number>;
  counts: Record<string, number>;
};

/** D13 answerability gate result (Python engine). RETRIEVAL_FAILED is distinct from INSUFFICIENT_EVIDENCE. */
export type AnswerabilityState = "ANSWERABLE" | "INSUFFICIENT_EVIDENCE" | "RETRIEVAL_FAILED";

export type RagAnswerability = {
  state: AnswerabilityState;
  reason: string;
  topRerankScore: number | null;
  tau: number;
  /** false until T17 sets tau from dev/validation runs: the UI says "gate uncalibrated". */
  tauCalibrated: boolean;
  /** true when no reranker score existed (gate failed open, INV-16). */
  gateDegraded: boolean;
};

/** One stored evidence item (a cited child span). `label` is unique across all searches of one chat turn. */
export type RagEvidenceItem = {
  id: string;
  label: string;
  sourceId: number;
  sourceName: string;
  /** The Source version the quote was taken from (T26 re-checks it). */
  sourceVersionId?: string | null;
  pageStart: number | null;
  pageEnd: number | null;
  headingPath: string[];
};

export type RagEvidence = {
  bundleId: string | null;
  items: RagEvidenceItem[];
  nextLabel: number;
};

export type RagToolResult = {
  type: typeof RAG_TOOL_RESULT_TYPE;
  traceId?: string;
  query: string;
  status: "retrieved" | "empty" | "insufficient" | "failed" | "unavailable";
  message?: string;
  context: string;
  timings: SearchDocumentsResult["timings"];
  topK: number;
  threshold: number;
  chunks: RagToolChunk[];
  engine?: "legacy" | "python";
  retrieval?: RagRetrievalMeta;
  /** Python engine only (T25). */
  answerability?: RagAnswerability;
  evidence?: RagEvidence;
};

export function extractRagToolResult(output: unknown): RagToolResult | null {
  if (!output || typeof output !== "object") return null;
  const candidate = output as Partial<RagToolResult>;
  if (candidate.type !== RAG_TOOL_RESULT_TYPE) return null;
  if (!Array.isArray(candidate.chunks) || !candidate.timings) return null;
  return candidate as RagToolResult;
}
