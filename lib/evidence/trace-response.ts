import { z } from "zod";

const finite = z.number().finite();
const rank = finite.int().positive().nullable();
const identifier = z.string().uuid().nullable();
const counts = new Set(["returned", "dense", "lexical", "fused", "reranked", "after_dedup", "packed", "context_tokens"]);
const candidate = z.object({
  id: z.string().uuid(), chunk_id: identifier, source_id: finite.int().positive().nullable(),
  source_name: z.string().nullable(), source_version_id: identifier, version_number: rank,
  dense_rank: rank, dense_score: finite.nullable(), lexical_rank: rank, lexical_score: finite.nullable(),
  fusion_rank: rank, rerank_rank: rank, rerank_score: finite.nullable(),
  selected: z.boolean(), source_unavailable: z.boolean(),
}).transform((item) => item.source_unavailable ? {
  ...item, chunk_id: null, source_id: null, source_name: null, source_version_id: null, version_number: null,
} : item);

const responseSchema = z.object({
  trace: z.object({
    id: z.string().uuid(), query: z.string(), profile: z.string().nullable(), config_hash: z.string().nullable(),
    status: z.string(), degraded: z.array(z.string()), created_at: z.string(),
    stage_ms: z.record(z.string(), finite.nonnegative()).nullable(),
    counts: z.record(z.string(), z.unknown()).nullable().transform((value) => value === null ? null :
      Object.fromEntries(Object.entries(value).filter(([key, count]) => counts.has(key) &&
        typeof count === "number" && Number.isFinite(count) && count >= 0))),
    cost: z.object({
      embed_tokens: finite.nonnegative(), rerank_tokens: finite.nonnegative(), usd: finite.nonnegative().nullable(),
      estimated: z.boolean(), prices_as_of: z.string(),
    }).nullable(),
  }),
  answerability: z.object({
    state: z.enum(["ANSWERABLE", "INSUFFICIENT_EVIDENCE", "RETRIEVAL_FAILED"]), top_rerank_score: finite.nullable(),
  }).nullable(),
  candidates: z.array(candidate).max(100), candidates_truncated: z.boolean(),
});

/** Allowlist diagnostics fields; document text and arbitrary stored trace metadata never leave this boundary. */
export function parseRetrievalTraceResponse(value: unknown) {
  const result = responseSchema.safeParse(value);
  return result.success ? result.data : null;
}
