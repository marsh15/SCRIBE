"use client";

import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ReuploadNote } from "@/components/source-ingest-status";
import { SourceVersionHistory } from "@/components/source-version-history";
import { Button } from "@/components/ui/button";
import type { ChunkKind, ChunkView, JobHistoryEntry } from "@/lib/files/source-detail";
import {
  buildBlockTree,
  describePages,
  describeSpan,
  type BlockTreeNode,
  type Page,
  type StructureBlock,
} from "@/lib/files/source-detail-paging";
import type { SourceVersionView } from "@/lib/ingestion/source-version-state";

/** Result shape of the T24 server actions (kept structural so tests can inject plain loaders). */
export type Loaded<T> = { ok: true; data: Page<T> } | { ok: false; status: number; error: string };
export type PageLoader<T> = (offset: number) => Promise<Loaded<T>>;

interface PagedState<T> {
  items: T[];
  nextOffset: number | null;
  loading: boolean;
  error: string | null;
  notFound: boolean;
  loadMore: () => void;
}

/** First page on mount (and whenever `load` changes); "Load more" appends. Stale responses are dropped. */
export function usePagedList<T>(load: PageLoader<T>): PagedState<T> {
  const [items, setItems] = useState<T[]>([]);
  const [nextOffset, setNextOffset] = useState<number | null>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const generation = useRef(0);

  const fetchAt = useCallback(
    async (offset: number, reset: boolean) => {
      const mine = reset ? ++generation.current : generation.current;
      setLoading(true);
      setError(null);
      try {
        const result = await load(offset);
        if (mine !== generation.current) return;
        if (!result.ok) {
          setNotFound(result.status === 404);
          setError(result.error);
          if (reset) setItems([]);
          setNextOffset(null);
          return;
        }
        setItems((prev) => (reset ? result.data.items : [...prev, ...result.data.items]));
        setNextOffset(result.data.nextOffset);
      } catch {
        if (mine !== generation.current) return;
        setError("Could not load this tab.");
      } finally {
        if (mine === generation.current) setLoading(false);
      }
    },
    [load]
  );

  useEffect(() => {
    setItems([]);
    setNextOffset(0);
    setNotFound(false);
    void fetchAt(0, true);
  }, [fetchAt]);

  const loadMore = useCallback(() => {
    if (nextOffset !== null) void fetchAt(nextOffset, false);
  }, [fetchAt, nextOffset]);

  return { items, nextOffset, loading, error, notFound, loadMore };
}

function Frame({ children, label }: { children: ReactNode; label: string }) {
  return (
    <section aria-label={label} className="mx-auto w-full max-w-4xl px-6 py-4 sm:px-12">
      {children}
    </section>
  );
}

function ListFooter<T>({ state, empty, noun }: { state: PagedState<T>; empty: string; noun: string }) {
  if (state.error) {
    return (
      <p role="alert" className="text-sm text-destructive" data-testid="panel-error">
        {state.notFound ? "Source not found." : state.error}
      </p>
    );
  }
  if (state.loading && state.items.length === 0) {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="panel-loading">
        <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> Loading {noun}
      </p>
    );
  }
  if (state.items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="panel-empty">
        {empty}
      </p>
    );
  }
  if (state.nextOffset === null) return null;
  return (
    <Button
      variant="outline"
      className="mt-3 min-h-11 rounded-sm px-4 text-sm"
      onClick={state.loadMore}
      disabled={state.loading}
      data-testid="load-more"
    >
      {state.loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> : null}
      Load more
    </Button>
  );
}

const badge = "rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs text-muted-foreground";

// ---------------------------------------------------------------------------------------------- Structure

export const OCR_WARNING = "OCR text — verify against original";

function BlockLine({ block }: { block: StructureBlock }) {
  return (
    <li className="py-1.5" data-testid="block" data-ocr={block.ocr ? "true" : "false"}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={badge}>{block.type}</span>
        <span className="font-mono text-xs text-muted-foreground">
          {block.page === null ? "page unavailable" : `p. ${block.page}`}
          {block.rowNumber !== null ? `, row ${block.rowNumber}` : ""}
        </span>
        {block.ocr && (
          <span
            className="rounded-sm border border-amber-500/40 bg-amber-50/60 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-950/20 dark:text-amber-400"
            data-testid="ocr-flag"
          >
            {OCR_WARNING}
          </span>
        )}
      </div>
      {block.preview && (
        <p className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground/80">
          {block.preview}
          {block.textLength > block.preview.length ? "…" : ""}
        </p>
      )}
    </li>
  );
}

function Section({ node }: { node: BlockTreeNode }) {
  return (
    <li data-testid="section" data-depth={node.depth}>
      <p className="mt-2 font-serif text-base text-foreground" role="heading" aria-level={Math.min(6, node.depth + 2)}>
        {node.title}
      </p>
      <div className="ml-3 border-l border-border/60 pl-3">
        {node.blocks.length > 0 && (
          <ul className="divide-y divide-border/30">
            {node.blocks.map((b) => (
              <BlockLine key={b.id} block={b} />
            ))}
          </ul>
        )}
        {node.children.length > 0 && (
          <ul>
            {node.children.map((child, i) => (
              <Section key={`${child.title}-${i}`} node={child} />
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

export function StructurePanel({ load }: { load: PageLoader<StructureBlock> }) {
  const state = usePagedList(load);
  const tree = buildBlockTree(state.items);
  return (
    <Frame label="Document structure">
      {tree.blocks.length > 0 && (
        <ul className="divide-y divide-border/30" data-testid="structure-tree">
          {tree.blocks.map((b) => (
            <BlockLine key={b.id} block={b} />
          ))}
        </ul>
      )}
      {tree.children.length > 0 && (
        <ul data-testid={tree.blocks.length === 0 ? "structure-tree" : undefined}>
          {tree.children.map((child, i) => (
            <Section key={`${child.title}-${i}`} node={child} />
          ))}
        </ul>
      )}
      <ListFooter state={state} noun="structure" empty="No structure yet. It appears once this Source has been indexed." />
    </Frame>
  );
}

// ---------------------------------------------------------------------------------------------- Chunks

const KINDS: Array<{ id: ChunkKind; label: string }> = [
  { id: "all", label: "All" },
  { id: "parent", label: "Parents" },
  { id: "child", label: "Children" },
];

function ChunkCard({ chunk }: { chunk: ChunkView }) {
  const pages = describePages(chunk.spans);
  return (
    <li className="rounded-sm border border-border/50 bg-card p-3" data-testid="chunk" data-kind={chunk.kind}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className={badge}>{chunk.kind === "parent" ? "Parent" : "Child"}</span>
        <span className="font-mono text-xs text-foreground">#{chunk.chunkIndex}</span>
        {chunk.tokenCount !== null && <span className="font-mono text-xs text-muted-foreground">{chunk.tokenCount} tokens</span>}
        {chunk.kind === "parent" && chunk.childCount !== null && (
          <span className="font-mono text-xs text-muted-foreground">{chunk.childCount} children</span>
        )}
        <span className="font-mono text-xs text-muted-foreground" data-testid="chunk-pages">
          {pages ?? "page unavailable"}
        </span>
      </div>
      {chunk.headingText && <p className="mt-1 font-serif text-sm text-foreground">{chunk.headingText}</p>}
      {chunk.spans.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5" aria-label="Source spans" data-testid="chunk-spans">
          {chunk.spans.map((span, i) => (
            <li key={i} className="font-mono text-xs text-muted-foreground">
              {describeSpan(span)}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 whitespace-pre-wrap break-words text-sm text-foreground/80">
        {chunk.excerpt}
        {chunk.contentLength > chunk.excerpt.length ? "…" : ""}
      </p>
    </li>
  );
}

export function ChunksPanel({
  makeLoader,
  legacyChunks,
}: {
  makeLoader: (kind: ChunkKind) => PageLoader<ChunkView>;
  legacyChunks?: Array<{ id: number; chunkIndex: number; content: string }>;
}) {
  const [kind, setKind] = useState<ChunkKind>("all");
  const load = useCallback((offset: number) => makeLoader(kind)(offset), [makeLoader, kind]);
  const state = usePagedList(load);
  const showLegacy = !state.loading && !state.error && state.items.length === 0 && kind === "all" && (legacyChunks?.length ?? 0) > 0;
  return (
    <Frame label="Indexed chunks">
      <div role="group" aria-label="Chunk type" className="mb-3 flex gap-1">
        {KINDS.map((k) => (
          <Button
            key={k.id}
            variant={kind === k.id ? "secondary" : "ghost"}
            aria-pressed={kind === k.id}
            className="min-h-11 rounded-sm px-3 text-sm"
            onClick={() => setKind(k.id)}
          >
            {k.label}
          </Button>
        ))}
      </div>
      {state.items.length > 0 && (
        <ol className="space-y-2" data-testid="chunk-list">
          {state.items.map((chunk) => (
            <ChunkCard key={chunk.id} chunk={chunk} />
          ))}
        </ol>
      )}
      {showLegacy ? (
        <div data-testid="legacy-chunks">
          <p className="mb-2 text-sm text-muted-foreground">
            This Source uses the older index, so these are its legacy chunks without spans. Upload it again to upgrade.
          </p>
          <ol className="space-y-2">
            {legacyChunks!.map((c) => (
              <li key={c.id} className="rounded-sm border border-border/50 bg-card p-3 text-sm text-foreground/80">
                <span className="mr-2 font-mono text-xs text-muted-foreground">#{c.chunkIndex}</span>
                {c.content.length > 400 ? `${c.content.slice(0, 400)}…` : c.content}
              </li>
            ))}
          </ol>
        </div>
      ) : (
        <ListFooter state={state} noun="chunks" empty="No chunks yet. They appear once this Source has been indexed." />
      )}
    </Frame>
  );
}

// ---------------------------------------------------------------------------------------------- Versions

export function VersionsPanel({ load, sourceId }: { load: PageLoader<SourceVersionView>; sourceId: number }) {
  const state = usePagedList(load);
  return (
    <Frame label="Version history">
      {state.items.length > 0 && (
        <SourceVersionHistory
          versions={state.items}
          renderAction={(v) =>
            v.uploadState === "stored" ? (
              <a
                href={`/api/files/${sourceId}/view?version=${v.versionNumber}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex min-h-6 items-center text-xs text-muted-foreground underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                data-testid={`open-original-${v.versionNumber}`}
              >
                Open original{v.state === "duplicate" ? " (shared with the earlier version)" : ""}
              </a>
            ) : null
          }
        />
      )}
      <ListFooter state={state} noun="versions" empty="No version history yet." />
    </Frame>
  );
}

// ---------------------------------------------------------------------------------------------- History

const STEPS = ["PARSE", "CHUNK", "EMBED", "DONE"] as const;
const STEP_LABEL: Record<string, string> = {
  PARSE: "Read",
  CHUNK: "Split",
  EMBED: "Embed",
  DONE: "Done",
  FAILED: "Stopped",
  CANCELLED: "Cancelled",
  SUPERSEDED: "Replaced",
};

function formatTime(value: string | null) {
  return value ? new Date(value).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : null;
}

function JobEntry({ job }: { job: JobHistoryEntry }) {
  const stage = job.stage ?? "";
  const reached = STEPS.indexOf(stage as (typeof STEPS)[number]);
  const terminal = reached === -1 && stage !== "" ? stage : null;
  const failed = job.status === "failed";
  return (
    <li className="rounded-sm border border-border/50 bg-card p-3" data-testid="job" data-status={job.status}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-sm text-foreground">{job.versionNumber !== null ? `v${job.versionNumber}` : "Job"}</span>
        <span className={badge} data-testid="job-status">
          {job.status}
        </span>
        <span className="font-mono text-xs text-muted-foreground" data-testid="job-attempts">
          Attempts: {job.attempts}
        </span>
        {job.errorCode && (
          <span className="font-mono text-xs text-destructive" data-testid="job-error-code">
            {job.errorCode}
          </span>
        )}
      </div>
      <ol className="mt-2 flex flex-wrap gap-1" aria-label="Pipeline steps">
        {STEPS.map((step, i) => {
          const done = reached >= 0 ? i < reached || (step === "DONE" && reached === STEPS.length - 1) : false;
          const current = reached === i && step !== "DONE";
          return (
            <li
              key={step}
              aria-current={current ? "step" : undefined}
              data-step={step}
              data-state={done ? "done" : current ? (failed ? "failed" : "current") : "pending"}
              className={`rounded-sm px-1.5 py-0.5 text-xs ${
                done ? "bg-rag/15 text-rag" : current ? (failed ? "bg-destructive/10 text-destructive" : "border border-rag text-foreground") : "bg-muted text-muted-foreground"
              }`}
            >
              {STEP_LABEL[step]}
              {done ? " (done)" : current ? (failed ? " (failed)" : " (in progress)") : ""}
            </li>
          );
        })}
        {terminal && (
          <li className="rounded-sm bg-muted px-1.5 py-0.5 text-xs text-muted-foreground" data-step={terminal}>
            {STEP_LABEL[terminal] ?? terminal}
          </li>
        )}
      </ol>
      {job.lastError && <p className="mt-2 break-words font-mono text-xs text-muted-foreground" data-testid="job-last-error">{job.lastError}</p>}
      <p className="mt-1 font-mono text-xs text-muted-foreground">
        {[
          formatTime(job.createdAt) && `Queued ${formatTime(job.createdAt)}`,
          formatTime(job.startedAt) && `started ${formatTime(job.startedAt)}`,
          formatTime(job.finishedAt) && `finished ${formatTime(job.finishedAt)}`,
          formatTime(job.nextRetryAt) && `next retry ${formatTime(job.nextRetryAt)}`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
    </li>
  );
}

export function HistoryPanel({
  load,
  ocrPages,
  reuploadRequiredAt,
  reuploadReason,
}: {
  load: PageLoader<JobHistoryEntry>;
  ocrPages: number;
  reuploadRequiredAt?: string | null;
  reuploadReason?: string | null;
}) {
  const state = usePagedList(load);
  return (
    <Frame label="Indexing history">
      <p className="mb-2 text-sm text-muted-foreground" data-testid="ocr-summary">
        {ocrPages > 0
          ? `${ocrPages} ${ocrPages === 1 ? "page was" : "pages were"} read with OCR. ${OCR_WARNING}.`
          : "No pages needed OCR."}
      </p>
      {reuploadRequiredAt && (
        <div className="mb-3">
          <ReuploadNote reuploadRequiredAt={reuploadRequiredAt} />
          {reuploadReason && <p className="text-xs text-muted-foreground">{reuploadReason}</p>}
        </div>
      )}
      {state.items.length > 0 && (
        <ol className="space-y-2" data-testid="job-timeline">
          {state.items.map((job) => (
            <JobEntry key={job.id} job={job} />
          ))}
        </ol>
      )}
      <ListFooter state={state} noun="history" empty="No indexing jobs yet." />
    </Frame>
  );
}
