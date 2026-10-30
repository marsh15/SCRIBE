import { z } from "zod";

const finite = z.number().finite();
const nullableNumber = finite.nullable();
const candidate = z.object({
  source_name: z.string().nullable(), source_version_id: z.string().nullable(), version_number: nullableNumber,
  dense_rank: nullableNumber, lexical_rank: nullableNumber, fusion_rank: nullableNumber, rerank_rank: nullableNumber,
  dense_score: nullableNumber.optional(), lexical_score: nullableNumber.optional(), rerank_score: nullableNumber.optional(),
  selected: z.boolean(), source_unavailable: z.boolean(),
});
const traceSchema = z.object({
  trace: z.object({
    id: z.string(), cost: z.object({ embed_tokens: nullableNumber.optional(), rerank_tokens: nullableNumber.optional(),
      usd: nullableNumber, estimated: z.boolean(), prices_as_of: z.string().nullable().optional() }).nullable().optional(),
  }),
  candidates: z.array(candidate), candidates_truncated: z.boolean(),
});
export type RetrievalTraceDetails = z.infer<typeof traceSchema>;
export function parseRetrievalTraceDetails(value: unknown): RetrievalTraceDetails | null {
  const parsed = traceSchema.safeParse(value);
  if (!parsed.success) return null;
  return {
    ...parsed.data,
    candidates: parsed.data.candidates.map((item) => item.source_unavailable
      ? { ...item, source_name: null, source_version_id: null, version_number: null }
      : item),
  };
}
