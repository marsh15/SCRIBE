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

export type RagToolResult = {
  type: typeof RAG_TOOL_RESULT_TYPE;
  traceId?: string;
  query: string;
  status: "retrieved" | "empty" | "failed" | "unavailable";
  message?: string;
  context: string;
  timings: SearchDocumentsResult["timings"];
  topK: number;
  threshold: number;
  chunks: RagToolChunk[];
  engine?: "legacy" | "python";
  retrieval?: RagRetrievalMeta;
};

export function extractRagToolResult(output: unknown): RagToolResult | null {
  if (!output || typeof output !== "object") return null;
  const candidate = output as Partial<RagToolResult>;
  if (candidate.type !== RAG_TOOL_RESULT_TYPE) return null;
  if (!Array.isArray(candidate.chunks) || !candidate.timings) return null;
  return candidate as RagToolResult;
}
