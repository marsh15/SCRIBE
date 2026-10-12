"use client";

import type { UIMessage } from "@ai-sdk/react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Loader2, CheckCircle2, Circle, Zap, Brain, ExternalLink, Clock, Gauge, AlertTriangle } from "lucide-react";
import { useState, useEffect } from "react";
import {
  countKnowledgeBaseInvocations,
  getLastKnowledgeBaseInvocation,
} from "@/lib/chat-tools";
import { EMBEDDING_MODEL_LABEL } from "@/lib/embedding-config";
import {
  extractRagToolResult,
  type RagAnswerability,
  type RagRetrievalMeta,
  type RagToolChunk,
} from "@/lib/rag-types";

interface RAGInspectorProps {
  messages: UIMessage[];
  status: string;
  chatId?: string;
}

type PersistedTrace = {
  trace: {
    id: string;
    status: string;
    query: string;
    embeddingMs: number;
    retrievalMs: number;
    generationMs: number | null;
    totalMs: number | null;
    topK: number;
    threshold: number;
  };
  chunks: Array<{
    id: number;
    documentId: number;
    fileId: number;
    fileName: string;
    chunkIndex: number;
    similarity: number;
    rank: number;
    contentPreview: string;
    metadata: unknown;
  }>;
  evaluation: {
    status: string;
    judgeModel: string;
    groundednessScore: number | null;
    answerRelevanceScore: number | null;
    citationSupportScore: number | null;
    overallScore: number | null;
    verdict: string | null;
    rationale: string | null;
    error: string | null;
  } | null;
};

type TraceResponse = {
  trace: PersistedTrace | null;
};

export function RAGInspector({ messages, status, chatId }: RAGInspectorProps) {
  const isPendingStart = status === "submitted";
  const isStreaming = status === "streaming";
  const isLoading = isPendingStart || isStreaming;

  const lastToolInvocation = getLastKnowledgeBaseInvocation(messages);
  const liveToolResult = extractRagToolResult(lastToolInvocation?.result);

  const currentToolInvocation = isPendingStart ? undefined : lastToolInvocation;

  const isSearching = !!currentToolInvocation && !("result" in currentToolInvocation) && isLoading;
  const isActive = isLoading || isSearching;

  const [activeStep, setActiveStep] = useState(0);
  const [persistedTrace, setPersistedTrace] = useState<PersistedTrace | null>(null);

  useEffect(() => {
    if (isPendingStart || isSearching) {
      setActiveStep(1);
      const t1 = setTimeout(() => setActiveStep(2), 400);
      const t2 = setTimeout(() => setActiveStep(3), 900);
      return () => { clearTimeout(t1); clearTimeout(t2); };
    } else if (isStreaming) {
      setActiveStep(4);
      const t3 = setTimeout(() => setActiveStep(5), 400);
      return () => clearTimeout(t3);
    } else {
      setActiveStep(0);
    }
  }, [isPendingStart, isSearching, isStreaming]);

  useEffect(() => {
    if (!chatId) {
      setPersistedTrace(null);
      return;
    }

    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const fetchTrace = async (attempt = 0) => {
      try {
        const response = await fetch(`/api/rag/latest?chatId=${encodeURIComponent(chatId)}`, {
          cache: "no-store",
        });
        if (!response.ok) return;

        const data = (await response.json()) as TraceResponse;
        if (cancelled) return;

        setPersistedTrace(data.trace);

        const needsEval =
          !isLoading &&
          data.trace &&
          !data.trace.evaluation &&
          attempt < 5;

        if (needsEval) {
          retry = setTimeout(() => void fetchTrace(attempt + 1), 1200);
        }
      } catch (error) {
        console.error("[RAGInspector] Failed to load trace:", error);
      }
    };

    if (!isLoading || liveToolResult?.traceId) {
      void fetchTrace();
    }

    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
    };
  }, [chatId, isLoading, liveToolResult?.traceId, messages.length]);

  const totalMessages = messages.length;
  const toolInvocations = countKnowledgeBaseInvocations(messages);
  const trace = persistedTrace?.trace;
  const timings = trace
    ? {
        embeddingMs: trace.embeddingMs,
        retrievalMs: trace.retrievalMs,
        generationMs: trace.generationMs,
        totalMs: trace.totalMs,
      }
    : liveToolResult
      ? {
          embeddingMs: liveToolResult.timings.embeddingMs,
          retrievalMs: liveToolResult.timings.retrievalMs,
          generationMs: null,
          totalMs: liveToolResult.timings.totalMs,
        }
      : null;
  const displayChunks =
    !liveToolResult?.retrieval && persistedTrace?.chunks && persistedTrace.chunks.length > 0
      ? persistedTrace.chunks
      : liveToolResult?.chunks ?? [];
  const evaluation = persistedTrace?.evaluation ?? null;
  const evalPending =
    !isLoading &&
    !!(trace || liveToolResult?.traceId) &&
    !evaluation;

  return (
    <div className="flex flex-col h-full bg-muted/20 overflow-hidden">
      {/* Header */}
      <div
        className={`p-4 border-b transition-all duration-200 ${isActive ? "border-rag/30 bg-rag/5" : "border-border/50"
          }`}
      >
        <div className="flex items-center justify-between w-full">
          <span className="font-mono text-[10px] uppercase tracking-widest text-foreground/80">
            Evidence
          </span>
          <div
            className={`flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-widest transition-colors duration-200 ${isActive ? "text-rag" : "text-muted-foreground"
              }`}
          >
            <div
              className={`w-2 h-2 rounded-full transition-all duration-300 ${isActive
                ? "bg-rag"
                : "bg-border"
                }`}
            />
            {isActive ? "Active" : "Standby"}
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-4 space-y-6">
          {/* Pipeline */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Zap className="w-3 h-3" />
              Pipeline
            </h3>
            <div className="space-y-0 pl-1">
              <Step label="Tokenize Query" step={1} active={activeStep === 1} done={activeStep > 1 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label={`Embed (${EMBEDDING_MODEL_LABEL})`} step={2} active={activeStep === 2} done={activeStep > 2 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Vector Search (pgvector)" step={3} active={activeStep === 3} done={activeStep > 3 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Build Context" step={4} active={activeStep === 4} done={activeStep > 4 || (!isLoading && messages.length > 0)} isLast={false} />
              <Step label="Stream LLM Response" step={5} active={activeStep === 5} done={!isLoading && messages.length > 0} isLast={true} />
            </div>
          </section>

          {/* Session Stats */}
          <section>
            <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
              <Brain className="w-3 h-3" />
              Session
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="border border-border/50 bg-card p-3 rounded-sm hover:border-border/80 transition-colors">
                <div className="font-mono text-lg font-medium text-foreground tabular-nums">
                  {totalMessages}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  Messages
                </div>
              </div>
              <div className="border border-border/50 bg-card p-3 rounded-sm hover:border-rag/30 transition-colors">
                <div className="font-mono text-lg font-medium text-rag tabular-nums">
                  {Math.max(toolInvocations, messages.filter(m => m.role === "user").length)}
                </div>
                <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
                  RAG Calls
                </div>
              </div>
            </div>
          </section>

          {liveToolResult?.status === "unavailable" && (
            <div
              role="alert"
              className="flex items-start gap-2 rounded-sm border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{liveToolResult.message ?? "Search unavailable"}</span>
            </div>
          )}

          {liveToolResult?.retrieval && (
            <RetrievalDetails
              retrieval={liveToolResult.retrieval}
              chunks={liveToolResult.chunks}
              answerability={liveToolResult.answerability}
              evidenceLabels={liveToolResult.evidence?.items.map((i) => i.label)}
            />
          )}

          {!!timings && (
            <section className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
                <Clock className="w-3 h-3" />
                Latency
              </h3>
              <div className="grid grid-cols-2 gap-2">
                <Metric label="Embed" value={formatMs(timings.embeddingMs)} />
                <Metric label="Retrieve" value={formatMs(timings.retrievalMs)} />
                <Metric label="Generate" value={timings.generationMs === null ? "..." : formatMs(timings.generationMs)} />
                <Metric label="Total" value={timings.totalMs === null ? "..." : formatMs(timings.totalMs)} />
              </div>
            </section>
          )}

          {(evaluation || evalPending) && (
            <section className="animate-in fade-in slide-in-from-bottom-2 duration-300">
              <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3 flex items-center gap-1.5">
                <Gauge className="w-3 h-3" />
                Judge
              </h3>
              <div className="border border-border/50 bg-card p-3 rounded-sm space-y-3">
                {evalPending ? (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-rag" />
                    Evaluation pending
                  </div>
                ) : evaluation?.status === "completed" ? (
                  <>
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-[9px] uppercase text-muted-foreground">
                        Verdict
                      </span>
                      <span className="rounded-sm bg-rag/15 px-1.5 py-0.5 font-mono text-[9px] uppercase text-rag">
                        {evaluation.verdict ?? "unknown"}
                      </span>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <Metric label="Grounded" value={formatScore(evaluation.groundednessScore)} />
                      <Metric label="Relevant" value={formatScore(evaluation.answerRelevanceScore)} />
                      <Metric label="Citations" value={formatScore(evaluation.citationSupportScore)} />
                      <Metric label="Overall" value={formatScore(evaluation.overallScore)} />
                    </div>
                    {evaluation.rationale ? (
                      <p className="text-xs leading-relaxed text-muted-foreground">
                        {evaluation.rationale}
                      </p>
                    ) : null}
                  </>
                ) : (
                  <div className="space-y-1">
                    <div className="font-mono text-[9px] uppercase text-muted-foreground">
                      {evaluation?.status ?? "Unavailable"}
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {evaluation?.error ?? "No judge result is available for this trace."}
                    </p>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* Retrieved Chunks */}
          {displayChunks.length > 0 && (
            <section className="animate-in fade-in slide-in-from-bottom-2 duration-500">
              <h3 className="font-mono text-[10px] text-muted-foreground uppercase mb-3">
                Retrieved Chunks
              </h3>
              <div className="space-y-3">
                {displayChunks.map((chunk, i) => (
                  <div
                    key={`${chunk.fileId}-${chunk.documentId}-${chunk.rank}`}
                    className="border border-border/50 bg-card p-3 rounded-sm space-y-2 hover:border-rag/30 transition-colors duration-200"
                    style={{ animation: `stagger-in 0.4s ease-out ${i * 100}ms both` }}
                  >
                    <div className="flex justify-between items-center gap-2 text-xs">
                      <a href={`/files/${chunk.fileId}`} className="inline-flex min-h-11 min-w-0 items-center gap-1 truncate text-xs font-medium text-rag hover:underline">
                        <span className="truncate">{chunk.fileName}</span>
                        <ExternalLink className="h-3 w-3 shrink-0" aria-hidden="true" />
                      </a>
                      <span className="font-mono text-[9px] bg-rag/15 text-rag px-1.5 py-0.5 rounded-sm shrink-0">
                        #{chunk.rank}{"stageRanks" in chunk && chunk.stageRanks ? "" : ` / ${formatSimilarity(chunk.similarity)}`}
                      </span>
                    </div>
                    <div className="font-mono text-[9px] uppercase text-muted-foreground">
                      Chunk {chunk.chunkIndex + 1}
                    </div>
                    <p className="text-xs font-sans text-muted-foreground line-clamp-3 leading-relaxed">
                      {chunk.contentPreview}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Empty State */}
          {!lastToolInvocation && !persistedTrace && !isLoading && (
            <div className="h-40 flex flex-col items-center justify-center text-center opacity-40">
              <div className="grid grid-cols-4 gap-1.5 mb-4">
                {Array.from({ length: 16 }).map((_, i) => (
                  <div
                    key={i}
                    className="w-3 h-3 bg-foreground/20 rounded-sm"
                    style={{
                      animation: `stagger-in 0.5s ease-out ${i * 60}ms both`,
                    }}
                  />
                ))}
              </div>
              <p className="font-mono text-[10px] uppercase tracking-wider">
                Awaiting Query
              </p>
              <p className="font-mono text-[9px] text-muted-foreground mt-1">
                Send a message to activate the pipeline
              </p>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

// stage_ms keys as emitted by the Python pipeline (search.py, pipeline.py, retrieve_routes.py).
// Lexical has no standalone timer: it is timed together with the query embedding wait.
const STAGES: Array<{ key: string; label: string; msKeys: string[]; countKey?: string }> = [
  { key: "dense", label: "Dense", msKeys: ["dense"], countKey: "dense" },
  { key: "lexical", label: "Lexical", msKeys: ["lexical_plus_embed_wait"], countKey: "lexical" },
  { key: "fused", label: "Fused", msKeys: ["fusion"], countKey: "fused" },
  { key: "reranked", label: "Reranked", msKeys: ["rerank"], countKey: "reranked" },
  { key: "selected", label: "Selected", msKeys: ["pack"], countKey: "returned" },
];

function stageMs(stageMsMap: Record<string, number>, keys: string[]) {
  for (const k of keys) if (typeof stageMsMap[k] === "number") return stageMsMap[k];
  return null;
}

function rankCell(value: number | null | undefined) {
  return value === null || value === undefined ? "-" : String(value);
}

/** Stage timeline + per-stage candidate ranks for the Python engine (T16, INV-16). */
export function RetrievalDetails({
  retrieval,
  chunks,
  answerability,
  evidenceLabels,
}: {
  retrieval: RagRetrievalMeta;
  chunks: RagToolChunk[];
  answerability?: RagAnswerability;
  evidenceLabels?: string[];
}) {
  const degraded = retrieval.degraded.length > 0;
  return (
    <section data-testid="retrieval-details" className="space-y-3">
      <h3 className="font-mono text-[10px] text-muted-foreground uppercase flex items-center gap-1.5">
        <Zap className="w-3 h-3" />
        Retrieval
      </h3>
      <div className="flex flex-wrap items-center gap-2 font-mono text-[9px] uppercase">
        <span className="rounded-sm bg-rag/15 px-1.5 py-0.5 text-rag">{retrieval.profile}</span>
        {retrieval.configHash ? (
          <span className="rounded-sm bg-muted px-1.5 py-0.5 text-muted-foreground" title={retrieval.configHash}>
            cfg {retrieval.configHash.slice(0, 8)}
          </span>
        ) : null}
        {degraded ? (
          <span
            data-testid="degraded-badge"
            className="rounded-sm bg-destructive/15 px-1.5 py-0.5 text-destructive"
          >
            Degraded: {retrieval.degraded.join(", ")}
          </span>
        ) : null}
      </div>
      {answerability ? (
        <div
          data-testid="answerability"
          className="space-y-1 border border-border/50 bg-card px-2 py-1.5 font-mono text-[10px]"
        >
          <div className="flex flex-wrap items-center gap-2 uppercase">
            <span>Gate</span>
            <span
              data-testid="gate-state"
              className={
                answerability.state === "ANSWERABLE"
                  ? "rounded-sm bg-rag/15 px-1.5 py-0.5 text-rag"
                  : "rounded-sm bg-destructive/15 px-1.5 py-0.5 text-destructive"
              }
            >
              {answerability.state}
            </span>
            {!answerability.tauCalibrated ? (
              <span data-testid="gate-uncalibrated" className="rounded-sm bg-muted px-1.5 py-0.5 text-muted-foreground">
                gate uncalibrated
              </span>
            ) : null}
            {answerability.gateDegraded ? (
              <span data-testid="gate-degraded" className="rounded-sm bg-destructive/15 px-1.5 py-0.5 text-destructive">
                no rerank score
              </span>
            ) : null}
          </div>
          <div className="text-muted-foreground">
            {answerability.reason} · top score{" "}
            {answerability.topRerankScore === null ? "-" : answerability.topRerankScore.toFixed(3)} · τ{" "}
            {answerability.tau}
            {evidenceLabels && evidenceLabels.length > 0 ? ` · ${evidenceLabels.join(" ")}` : ""}
          </div>
        </div>
      ) : null}
      <ol aria-label="Stage timeline" className="space-y-1">
        {STAGES.map((stage) => {
          const ms = stageMs(retrieval.stageMs, stage.msKeys);
          const count = stage.countKey ? retrieval.counts[stage.countKey] : undefined;
          return (
            <li
              key={stage.key}
              data-testid={`stage-${stage.key}`}
              className="flex items-center justify-between border border-border/50 bg-card px-2 py-1 font-mono text-[10px]"
            >
              <span>{stage.label}</span>
              <span className="text-muted-foreground tabular-nums">
                {count !== undefined ? `${count} / ` : ""}
                {ms === null ? "-" : formatMs(ms)}
              </span>
            </li>
          );
        })}
      </ol>
      {chunks.length > 0 && (
        <table className="w-full border-collapse font-mono text-[9px]" aria-label="Candidates">
          <thead>
            <tr className="text-left uppercase text-muted-foreground">
              <th className="py-1 pr-1">#</th>
              <th className="py-1 pr-1">Source / section</th>
              <th className="py-1 px-1">Dense</th>
              <th className="py-1 px-1">Lex</th>
              <th className="py-1 px-1">Fused</th>
              <th className="py-1 px-1">Rerank</th>
              <th className="py-1 pl-1">Sel</th>
            </tr>
          </thead>
          <tbody>
            {chunks.map((chunk) => (
              <tr key={`${chunk.fileId}-${chunk.rank}`} className="border-t border-border/40 align-top">
                <td className="py-1 pr-1 tabular-nums">{chunk.rank}</td>
                <td className="py-1 pr-1">
                  <div className="truncate max-w-[120px]">{chunk.fileName}</div>
                  <div className="truncate max-w-[120px] text-muted-foreground">{chunk.section ?? "-"}</div>
                </td>
                <td className="py-1 px-1 tabular-nums">{rankCell(chunk.stageRanks?.dense)}</td>
                <td className="py-1 px-1 tabular-nums">{rankCell(chunk.stageRanks?.lexical)}</td>
                <td className="py-1 px-1 tabular-nums">{rankCell(chunk.stageRanks?.fused)}</td>
                <td className="py-1 px-1 tabular-nums">{rankCell(chunk.stageRanks?.reranked)}</td>
                <td className="py-1 pl-1">{chunk.selected ? "yes" : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function formatMs(value: number) {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${value}ms`;
}

function formatSimilarity(value: number) {
  return `${Math.round(value * 100)}%`;
}

function formatScore(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "...";
  return `${Math.round(value * 100)}%`;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border/50 bg-background/40 p-2 rounded-sm">
      <div className="font-mono text-sm font-medium text-foreground tabular-nums">
        {value}
      </div>
      <div className="font-mono text-[9px] uppercase text-muted-foreground mt-0.5">
        {label}
      </div>
    </div>
  );
}

function Step({
  label,
  step,
  active,
  done,
  isLast,
}: {
  label: string;
  step: number;
  active?: boolean;
  done?: boolean;
  isLast?: boolean;
}) {
  return (
    <div className="relative flex items-start gap-2.5">
      {/* Connecting line */}
      {!isLast && (
        <div className="absolute left-[7px] top-[18px] w-[2px] h-[calc(100%-2px)] bg-border/40" />
      )}
      <div
        className={`flex items-center gap-2.5 font-mono text-[10px] py-1.5 transition-all duration-300 relative z-10 ${active
          ? "text-rag"
          : done
            ? "text-foreground/80"
            : "text-muted-foreground/60"
          }`}
      >
        <div className="w-4 h-4 flex items-center justify-center shrink-0 bg-card rounded-full">
          {active ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-rag" />
          ) : done ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-rag" />
          ) : (
            <Circle className="w-3 h-3 text-border" />
          )}
        </div>
        <span>
          {step}. {label}
        </span>
      </div>
    </div>
  );
}
