"use client";

import { useEffect, useState } from "react";
import { parseRetrievalTraceDetails, type RetrievalTraceDetails } from "@/lib/evidence/diagnostics";
import type { RagToolResult } from "@/lib/rag-types";

const STAGE_NAMES: Record<string, string> = {
  embed: "Embedding", lexical_plus_embed_wait: "Lexical + embedding wait", dense: "Dense search",
  hydrate: "Hydrate", fusion: "Fusion", rerank: "Rerank", pack: "Pack", parents_prefetch: "Parent prefetch",
  trace_persist: "Trace persistence", request_total: "Request total", total: "Search total",
};
const number = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? String(value) : "unavailable";

export function AnswerRetrievalDiagnostics({ results }: { results: RagToolResult[] }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="border-t border-border/50 pt-2" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer font-mono text-[10px] uppercase text-muted-foreground focus-visible:outline-2 focus-visible:outline-ring">Search diagnostics</summary>
      {open && (results.length ? <div className="mt-2 space-y-3">{results.map((result, i) => (
        <SearchResult key={`${result.traceId ?? "search"}-${i}`} result={result} index={i} />
      ))}</div> : <p className="mt-2 text-muted-foreground">No search diagnostics are available for this answer.</p>)}
    </details>
  );
}

function SearchResult({ result, index }: { result: RagToolResult; index: number }) {
  const gate = result.answerability;
  const stages = Object.entries(result.retrieval?.stageMs ?? {}).filter(([, value]) => Number.isFinite(value));
  const counts = Object.entries(result.retrieval?.counts ?? {}).filter(([, value]) => typeof value === "number" && Number.isFinite(value));
  return (
    <section className="min-w-0 space-y-2 rounded-sm border border-border/50 p-2" aria-label={`Search ${index + 1}`}>
      <p className="break-words"><strong>Search query:</strong> {result.query || "Unavailable"}</p>
      <p>Status: {result.status}{result.message ? ` · ${result.message}` : ""}</p>
      {result.retrieval && <p className="break-words">Profile: {result.retrieval.profile}{result.retrieval.configHash ? ` · configuration ${result.retrieval.configHash}` : ""}{result.retrieval.degraded.length ? ` · degraded: ${result.retrieval.degraded.join(", ")}` : ""}</p>}
      {gate ? <p>Answerability: {gate.state} · {gate.reason}{gate.state !== "RETRIEVAL_FAILED" && <> · top rerank score {number(gate.topRerankScore)} · cutoff {number(gate.tau)}{gate.tauCalibrated ? "" : " (uncalibrated)"}{gate.gateDegraded ? " · rerank score unavailable" : ""}</>}</p> : <p className="text-muted-foreground">Answerability unavailable.</p>}
      {stages.length ? <div><p>Recorded stage timings (ms):</p><ul className="ml-3 list-disc">{stages.map(([key, value]) => <li key={key}>{STAGE_NAMES[key] ?? key}: {value}</li>)}</ul></div> : <p className="text-muted-foreground">Stage timings unavailable.</p>}
      {counts.length > 0 && <p>Recorded counts: {counts.map(([key, value]) => `${key}: ${value}`).join(" · ")}</p>}
      {result.engine !== "python" && !result.retrieval && <p>Search limit: {number(result.topK)} · similarity threshold: {number(result.threshold)} · embedding {number(result.timings.embeddingMs)} ms · retrieval {number(result.timings.retrievalMs)} ms · total {number(result.timings.totalMs)} ms</p>}
      {result.chunks.length > 0 && result.retrieval && <div><p>Candidate stage ranks (positions, not confidence; unavailable means not recorded):</p><ul className="mt-1 space-y-1">{result.chunks.map((chunk, i) => <li key={i} className="break-words">Candidate {chunk.rank}: dense {number(chunk.stageRanks?.dense)}, lexical {number(chunk.stageRanks?.lexical)}, fused {number(chunk.stageRanks?.fused)}, reranked {number(chunk.stageRanks?.reranked)} · {chunk.selected === true ? "selected" : chunk.selected === false ? "not selected" : "selection unavailable"}</li>)}</ul></div>}
      {result.traceId && result.retrieval?.engine === "python" ? <TraceDetails key={result.traceId} traceId={result.traceId} /> : <p className="text-muted-foreground">Stored trace details unavailable.</p>}
    </section>
  );
}

type Load = { state: "idle" | "loading" | "error" | "notFound" } | { state: "ready"; data: RetrievalTraceDetails };
function TraceDetails({ traceId }: { traceId: string }) {
  const [load, setLoad] = useState<Load>({ state: "idle" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!attempt) return;
    const controller = new AbortController();
    setLoad({ state: "loading" });
    fetch(`/api/retrieval-traces/${encodeURIComponent(traceId)}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        if (controller.signal.aborted) return;
        if (response.status === 404) return setLoad({ state: "notFound" });
        if (!response.ok) return setLoad({ state: "error" });
        const data = parseRetrievalTraceDetails(await response.json());
        if (!controller.signal.aborted) setLoad(data?.trace.id === traceId ? { state: "ready", data } : { state: "error" });
      }).catch(() => { if (!controller.signal.aborted) setLoad({ state: "error" }); });
    return () => controller.abort();
  }, [traceId, attempt]);
  const cost = load.state === "ready" ? load.data.trace.cost : undefined;
  return <div className="border-t border-border/50 pt-2">
    {(load.state === "idle" || load.state === "error" || load.state === "notFound") && <button type="button" className="min-h-11 underline focus-visible:outline-2 focus-visible:outline-ring" onClick={() => setAttempt((n) => n + 1)}>{load.state === "idle" ? "Load stored trace details" : "Retry stored trace"}</button>}
    {load.state === "loading" && <p role="status">Loading stored trace…</p>}
    {load.state === "error" && <p role="alert">Stored trace could not be loaded.</p>}
    {load.state === "notFound" && <p role="alert">Stored trace not found or unavailable.</p>}
    {load.state === "ready" && <div className="space-y-1">
      <p>{cost ? `Retrieval cost: ${cost.usd === null ? "unavailable" : `$${cost.usd}`}${cost.estimated ? " (estimated)" : ""}${cost.prices_as_of ? ` · prices as of ${cost.prices_as_of}` : ""}` : "Retrieval cost unavailable."}</p>
      {cost && <p>Embedding tokens: {number(cost.embed_tokens)} · rerank tokens: {number(cost.rerank_tokens)}{cost.estimated ? " (estimated)" : ""}</p>}
      <p className="text-muted-foreground">Dense similarity, lexical relevance and reranker scores use different scales; they are not claim-verification scores.</p>
      {load.data.candidates.map((candidate, i) => <div key={i} className="break-words">
        <p>{candidate.source_unavailable ? "Source unavailable" : candidate.source_name ?? "Source unavailable"}{!candidate.source_unavailable && candidate.version_number != null ? ` · version ${candidate.version_number}` : !candidate.source_unavailable && candidate.source_version_id ? ` · version ID ${candidate.source_version_id}` : ""} · dense {number(candidate.dense_rank)}, lexical {number(candidate.lexical_rank)}, fused {number(candidate.fusion_rank)}, reranked {number(candidate.rerank_rank)} · {candidate.selected ? "selected" : "not selected"}</p>
        <p>Recorded scores: dense {number(candidate.dense_score)} · lexical {number(candidate.lexical_score)} · reranker {number(candidate.rerank_score)}</p>
      </div>)}
      {load.data.candidates_truncated && <p>Additional candidates omitted.</p>}
    </div>}
  </div>;
}
