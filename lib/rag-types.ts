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
};

export type RagToolResult = {
  type: typeof RAG_TOOL_RESULT_TYPE;
  traceId?: string;
  query: string;
  status: "retrieved" | "empty" | "failed";
  message?: string;
  context: string;
  timings: SearchDocumentsResult["timings"];
  topK: number;
  threshold: number;
  chunks: RagToolChunk[];
};

export function extractRagToolResult(output: unknown): RagToolResult | null {
  if (!output || typeof output !== "object") return null;
  const candidate = output as Partial<RagToolResult>;
  if (candidate.type !== RAG_TOOL_RESULT_TYPE) return null;
  if (!Array.isArray(candidate.chunks) || !candidate.timings) return null;
  return candidate as RagToolResult;
}
