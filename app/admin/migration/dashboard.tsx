"use client";

import { useEffect, useState } from "react";
import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";

interface MigrationSource {
  sourceId: number;
  name: string;
  state: string;
  stage: string;
  embeddedChunks: number;
  expectedChunks: number;
  progressPct: number | null;
  errorCode: string | null;
}

interface MigrationData {
  sources: MigrationSource[];
  totals: {
    sources: number;
    embeddedChunks: number;
    expectedChunks: number;
    progressPct: number | null;
    failures: number;
  };
  failures: Array<{ sourceId: number; name: string; stage: string; errorCode: string | null; lastError: string | null }>;
}

const LABELS: Record<string, string> = {
  upgraded: "Finished",
  building: "In progress",
  pending: "Waiting",
  failed: "Failed",
  stalled: "Needs attention",
  no_blob: "Original file unavailable",
  reupload: "Re-upload required",
  newer_upload: "Newer upload pending",
};

function Progress({ label, value, embedded, expected }: { label: string; value: number | null; embedded: number; expected: number }) {
  return (
    <div className="mt-2">
      {value === null ? (
        <p className="text-xs text-muted-foreground">Progress unavailable · {embedded} embedded · expected count unknown</p>
      ) : (
        <>
          <div
            className="h-2 overflow-hidden rounded-sm bg-muted"
            role="progressbar"
            aria-label={`${label} progress`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={value}
            aria-valuetext={`${value}%; ${embedded} of ${expected} passages embedded`}
          >
            <div className="h-full bg-rag transition-[width]" style={{ width: `${value}%` }} />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{value}% · {embedded} of {expected} passages embedded</p>
        </>
      )}
    </div>
  );
}

export function MigrationDashboard() {
  const [data, setData] = useState<MigrationData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState<number | null>(null);
  const [pollRevision, setPollRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const response = await fetch("/api/admin/migration", { cache: "no-store" });
        if (!response.ok) throw new Error("Could not load migration progress");
        const next = await response.json() as MigrationData;
        if (cancelled) return;
        setData(next);
        setError(null);
        const pending = next.sources.some((source) => ["building", "pending", "stalled"].includes(source.state));
        if (pending) timeout = setTimeout(poll, 5000);
      } catch {
        if (cancelled) return;
        setError("Could not load migration progress. Try refreshing.");
        timeout = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timeout) clearTimeout(timeout);
    };
  }, [pollRevision]);

  const retry = async (sourceId: number) => {
    setRetrying(sourceId);
    try {
      const response = await fetch("/api/admin/migration", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sourceId }),
      });
      if (!response.ok) throw new Error("Could not retry this migration job");
      setPollRevision((revision) => revision + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not retry this migration job");
    } finally {
      setRetrying(null);
    }
  };

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="font-serif text-4xl">Migration progress</h1>
        <p className="mt-2 text-sm text-muted-foreground">Structural index backfill · structural-v1</p>
        {error && <p className="mt-4 text-sm text-destructive" role="alert">{error}</p>}
        {!data ? (
          <p className="mt-8 text-sm text-muted-foreground" role="status">Loading migration progress…</p>
        ) : (
          <>
            <section className="mt-8 rounded-sm border border-border bg-card p-5" aria-label="Overall migration progress">
              <div className="flex flex-wrap justify-between gap-3">
                <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground">{data.totals.sources} sources · {data.totals.failures} failures</p>
                <p className="font-mono text-xs text-muted-foreground">{data.totals.embeddedChunks} embedded · {data.totals.expectedChunks} expected</p>
              </div>
              <Progress label="Overall migration" value={data.totals.progressPct} embedded={data.totals.embeddedChunks} expected={data.totals.expectedChunks} />
            </section>

            <section className="mt-8 space-y-3" aria-label="Sources">
              {data.sources.map((source) => (
                <article key={source.sourceId} className="rounded-sm border border-border bg-card p-4">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-medium">{source.name}</h2>
                      <p className="mt-1 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
                        {LABELS[source.state] ?? source.state} · Stage: {source.stage}
                      </p>
                    </div>
                  </div>
                  <Progress label={source.name} value={source.progressPct} embedded={source.embeddedChunks} expected={source.expectedChunks} />
                </article>
              ))}
              {data.sources.length === 0 && <p className="text-sm text-muted-foreground">No sources need migration.</p>}
            </section>

            {data.failures.length > 0 && (
              <section className="mt-10" aria-label="Migration failures">
                <h2 className="font-serif text-2xl">Failures</h2>
                <div className="mt-3 space-y-2">
                  {data.failures.map((failure) => (
                    <div key={failure.sourceId} className="rounded-sm border border-destructive/30 bg-card p-4">
                      <p className="text-sm">{failure.name} · {failure.stage}{failure.errorCode ? ` · ${failure.errorCode}` : ""}</p>
                      {failure.lastError && <p className="mt-1 text-xs text-muted-foreground">{failure.lastError}</p>}
                      <Button className="mt-3" variant="outline" size="sm" onClick={() => void retry(failure.sourceId)} disabled={retrying === failure.sourceId}>
                        {retrying === failure.sourceId ? <Loader2 className="mr-2 h-3 w-3 animate-spin" /> : <RotateCcw className="mr-2 h-3 w-3" />}
                        Retry
                      </Button>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}
