/**
 * T29: types and pure helpers for the profile comparison (Retrieval lab). The shapes mirror POST /compare in the
 * Python service (snake_case is kept: the BFF passes the service's JSON through unchanged).
 */

export const COMPARE_MIN_PROFILES = 2;
export const COMPARE_MAX_PROFILES = 5;

/** The seeded retrieval profiles (immutable configs, ADR-0006). The service is the source of truth; this is the UI list. */
export const COMPARE_PROFILES: ReadonlyArray<{ name: string; label: string; summary: string }> = [
  { name: "dense-only", label: "Dense only", summary: "Embedding similarity, top 40, no rerank" },
  { name: "lexical-only", label: "Lexical only", summary: "Postgres full-text search, top 40, no rerank" },
  { name: "hybrid-rrf", label: "Hybrid (RRF)", summary: "Dense and lexical fused by rank (k=60), no rerank" },
  { name: "dense-rerank", label: "Dense + rerank", summary: "Dense top 40, reranked top 20" },
  { name: "hybrid-rrf-rerank", label: "Hybrid + rerank", summary: "RRF, rerank top 20, parent expansion, packed context (default)" },
  { name: "lexical-only-v2", label: "Lexical only v2", summary: "Full-text OR-ranked with identifier boost, top 40, no rerank" },
  { name: "hybrid-rrf-v2", label: "Hybrid (RRF) v2", summary: "Dense and OR-ranked lexical fused by rank (k=60), no rerank" },
  { name: "hybrid-rrf-rerank-v2", label: "Hybrid + rerank v2", summary: "Hybrid + rerank with OR-ranked lexical and parent expansion" },
  { name: "hybrid-rrf-rerank-flat", label: "Hybrid + rerank (flat)", summary: "Hybrid + rerank without parent expansion" },
  { name: "hybrid-rrf-rerank-flat-v2", label: "Hybrid + rerank flat v2", summary: "Flat hybrid + rerank with OR-ranked lexical" },
];

export type MovementDirection = "same" | "up" | "down" | "new" | "dropped" | "absent";

export type Movement = { direction: MovementDirection; delta: number | null; places: number | null };

export type CompareResult = {
  rank: number;
  chunk_id: string;
  doc_name: string;
  heading_text: string | null;
  snippet: string;
  source_id: number;
  dense_rank: number | null;
  dense_score: number | null;
  lexical_rank: number | null;
  lexical_score: number | null;
  fusion_rank: number | null;
  fusion_score: number | null;
  rerank_rank: number | null;
  rerank_score: number | null;
  in_context: boolean;
};

export type CompareCost = {
  estimated: boolean;
  note: string;
  rerank_documents: number;
  rerank_tokens: number;
  usd: number | null;
};

export type CompareProfileResult = {
  profile: string;
  description: string;
  config_hash: string;
  trace_id: string | null;
  degraded: string[];
  stage_ms: Record<string, number>;
  counts: Record<string, number>;
  meta: Record<string, unknown>;
  cost: CompareCost;
  results: CompareResult[];
};

export type AlignmentRow = {
  chunk_id: string;
  doc_name: string;
  heading_text: string | null;
  snippet: string;
  source_id: number;
  ranks: Record<string, number | null>;
  movement: Record<string, Movement>;
  present_in: number;
  best_rank: number;
  worst_rank: number;
  rank_spread: number;
};

export type AlignmentSummary = {
  same: number;
  up: number;
  down: number;
  new: number;
  dropped: number;
  returned: number;
  overlap_with_baseline: number;
  jaccard_with_baseline: number;
};

export type CompareResponse = {
  compare_id: string;
  query: string;
  limit: number;
  profiles: string[];
  config_hash: string;
  snapshot: { id: string | null; isolation: string; read_only: boolean; shared_by: string[] };
  shared: { embedding: { calls: number; dims: number; ms: number | null; shared_by: string[]; is_fake: boolean } };
  profile_results: CompareProfileResult[];
  alignment: { baseline: string; rows: AlignmentRow[]; summary: Record<string, AlignmentSummary> };
  cost: { estimated: boolean; note: string; embed_tokens: number; embed_usd: number | null; rerank_tokens: number; usd: number | null };
  request_total_ms: number;
};

export type CompareRequest = { query: string; profiles: string[]; limit?: number; sourceIds?: number[] };

/** Text for a movement relative to the baseline. Never colour alone: the arrow, the words and the number all say it. */
export function describeMovement(m: Movement | undefined, isBaseline: boolean): { arrow: string; text: string } {
  if (isBaseline) return { arrow: "", text: "baseline" };
  switch (m?.direction) {
    case "up":
      return { arrow: "↑", text: `up ${m.places} ${m.places === 1 ? "place" : "places"}` };
    case "down":
      return { arrow: "↓", text: `down ${m.places} ${m.places === 1 ? "place" : "places"}` };
    case "same":
      return { arrow: "=", text: "same rank" };
    case "new":
      return { arrow: "+", text: "new, not in baseline" };
    case "dropped":
      return { arrow: "−", text: "dropped, not in this list" };
    default:
      return { arrow: "", text: "not returned" };
  }
}

const DEGRADED_LABELS: Record<string, string> = {
  rerank_unavailable: "Rerank unavailable: showing fused order",
  parent_expansion_unavailable: "Parent expansion unavailable",
  trace_persist_failed: "Trace not saved",
  ann_unavailable: "Approximate index unavailable: exact search used",
};

export function degradedLabel(flag: string): string {
  return DEGRADED_LABELS[flag] ?? `Degraded: ${flag.replace(/_/g, " ")}`;
}

export function formatCost(c: { usd: number | null; note: string }): string {
  if (c.usd === null) return "unknown (no unit price configured)";
  return `$${c.usd.toFixed(c.usd === 0 ? 2 : 6)}`;
}

/** Stage timings in a stable, readable order; unknown stages follow alphabetically. */
const STAGE_ORDER = ["embed", "lexical_plus_embed_wait", "dense", "hydrate", "read_in_snapshot", "parents_prefetch", "fusion", "rerank", "dedup", "expand", "pack", "trace_persist"];
export function orderedStages(stageMs: Record<string, number>): Array<[string, number]> {
  const known = STAGE_ORDER.filter((k) => k in stageMs && k !== "total");
  const rest = Object.keys(stageMs).filter((k) => !STAGE_ORDER.includes(k) && k !== "total").sort();
  return [...known, ...rest].map((k) => [k, stageMs[k]] as [string, number]);
}
