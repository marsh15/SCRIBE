import { AlertCircle, CheckCircle2, XCircle } from "lucide-react";
import { RunStamp, formatWhen, modelList, shortCommit, shortHash } from "@/components/eval/badges";
import {
  failureCounts,
  formatInterval,
  formatMetric,
  metricNames,
  type EvalCaseResult,
  type EvalRun,
} from "@/lib/eval/model";

export function RunHeader({ run }: { run: EvalRun }) {
  const prompts = Object.entries(run.promptVersions).map(([k, v]) => `${k}: ${v}`).join(", ");
  return (
    <section aria-labelledby="run-heading" className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 id="run-heading" className="text-xl font-semibold">{run.profileName}</h1>
        <RunStamp run={run} />
      </div>
      {!run.measurement ? (
        <p role="note" className="flex items-start gap-2 rounded-md border border-destructive bg-destructive/10 p-3 text-sm">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
          <span>
            This run is <strong>not a measurement</strong>
            {run.seeded ? " (seeded test data used to exercise this page)" : " (a fake component was used)"}. Its numbers show that
            the plumbing works, not how well Scribe performs. It is never highlighted as a best run.
          </span>
        </p>
      ) : null}
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-3">
        <Item label="Run id" value={run.id} mono />
        <Item label="Kind" value={run.kind === "answer" ? "Answer-level" : "Retrieval"} />
        <Item label="Dataset version / split" value={`${run.datasetVersion} / ${run.split} (${run.caseCount} cases)`} />
        <Item label="Config hash" value={shortHash(run.configHash)} mono />
        <Item label="Code commit" value={shortCommit(run.commit)} mono />
        <Item label="Started" value={formatWhen(run.startedAt)} />
        <Item label="Model ids" value={modelList(run.modelIds)} />
        <Item label="Prompt versions" value={prompts || "none recorded"} />
      </dl>
    </section>
  );
}

function Item({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={mono ? "break-all font-mono text-xs" : ""}>{value}</dd>
    </div>
  );
}

export function CategoryTable({ run }: { run: EvalRun }) {
  const metrics = metricNames(run.kind);
  const cats = Object.keys(run.perCategory).sort();
  return (
    <section aria-labelledby="cat-heading" className="space-y-2">
      <h2 id="cat-heading" className="text-base font-semibold">Overall and per category</h2>
      <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Per-category metrics, scrollable">
        <table className="w-full min-w-[720px] border-collapse text-left text-sm">
          <caption className="sr-only">
            Mean of each metric overall and for each question category, with 95 percent bootstrap confidence intervals.
          </caption>
          <thead>
            <tr className="border-b border-border bg-muted/50 text-xs">
              <th scope="col" className="p-3 font-semibold">Category</th>
              <th scope="col" className="p-3 font-semibold">Cases</th>
              {metrics.map((m) => (
                <th key={m} scope="col" className="p-3 font-semibold">{m}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-border bg-muted/30 font-medium">
              <th scope="row" className="p-3 text-left">Overall</th>
              <td className="p-3">{run.overall[metrics[0]]?.n ?? "n/a"}</td>
              {metrics.map((m) => (
                <td key={m} className="p-3 font-mono text-xs">{formatInterval(run.overall[m])}</td>
              ))}
            </tr>
            {cats.map((c) => (
              <tr key={c} className="border-b border-border last:border-b-0">
                <th scope="row" className="p-3 text-left font-normal">{c}</th>
                <td className="p-3">{run.perCategory[c][metrics[0]]?.n ?? "n/a"}</td>
                {metrics.map((m) => (
                  <td key={m} className="p-3 font-mono text-xs">{formatInterval(run.perCategory[c][m])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {run.abstention ? (
        <p className="text-xs text-muted-foreground">
          False-abstain rate {formatMetric(run.abstention.falseAbstainRate)}; abstention recall{" "}
          {formatMetric(run.abstention.abstentionRecall)}.
        </p>
      ) : null}
    </section>
  );
}

export function FailureSummary({ results }: { results: EvalCaseResult[] }) {
  const counts = failureCounts(results);
  return (
    <section aria-labelledby="fail-sum-heading" className="space-y-2">
      <h2 id="fail-sum-heading" className="text-base font-semibold">Failing questions by category</h2>
      {counts.length === 0 ? (
        <p className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="size-4" aria-hidden="true" /> No failing questions recorded for this run.
        </p>
      ) : (
        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {counts.map((c) => (
            <li key={c.category} className="flex items-center justify-between rounded-md border border-border p-3 text-sm">
              <span className="flex items-center gap-2">
                <XCircle className="size-4" aria-hidden="true" />
                {c.category}
              </span>
              <span className="font-mono">{c.count}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function FailureDrilldown({ results, kind }: { results: EvalCaseResult[]; kind: EvalRun["kind"] }) {
  const failing = results.filter((r) => r.failure);
  if (failing.length === 0) return null;
  return (
    <section aria-labelledby="fail-heading" className="space-y-2">
      <h2 id="fail-heading" className="text-base font-semibold">Failing questions ({failing.length})</h2>
      <p className="text-xs text-muted-foreground">
        Each question is classified from its result and the gold evidence. &quot;unclassified&quot; means the stored data did not
        identify a cause; nothing is guessed. Open a question for its candidates.
      </p>
      <ul className="space-y-2">
        {failing.map((r) => (
          <li key={r.caseId}>
            <details className="rounded-md border border-border">
              <summary className="flex min-h-11 cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 p-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
                <span className="font-mono text-xs">{r.caseId}</span>
                <span className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5 text-xs">
                  <XCircle className="size-3" aria-hidden="true" />
                  {r.failure}
                </span>
                <span className="text-muted-foreground">{r.category}</span>
                <span className="basis-full text-xs">{r.question ?? "Question text not available"}</span>
              </summary>
              <div className="space-y-3 border-t border-border p-3 text-xs">
                {r.traceId ? <p>Trace id: <code className="font-mono">{r.traceId}</code></p> : <p className="text-muted-foreground">No trace id was recorded for this question.</p>}
                {r.answer !== null ? (
                  <p>
                    <span className="font-semibold">Answer{r.abstained ? " (abstained)" : ""}:</span> {r.answer}
                  </p>
                ) : null}
                {r.claims.length ? (
                  <ul className="list-disc space-y-0.5 pl-5">
                    {r.claims.map((c, i) => (
                      <li key={i}>
                        <span className="font-mono">{c.status ?? "no status"}</span> {c.text}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {kind === "retrieval" ? <CandidateTable result={r} /> : null}
              </div>
            </details>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CandidateTable({ result }: { result: EvalCaseResult }) {
  if (result.candidates.length === 0) {
    return <p className="text-muted-foreground">No candidates were stored for this question (the ranking was empty).</p>;
  }
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`Candidates for ${result.caseId}, scrollable`}>
      <table className="w-full min-w-[560px] border-collapse text-left">
        <caption className="sr-only">Top candidates retrieved for {result.caseId}, best first, and whether each matches gold evidence.</caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="p-2 font-semibold">Rank</th>
            <th scope="col" className="p-2 font-semibold">Document</th>
            <th scope="col" className="p-2 font-semibold">Score</th>
            <th scope="col" className="p-2 font-semibold">Gold evidence</th>
            <th scope="col" className="p-2 font-semibold">Passage</th>
          </tr>
        </thead>
        <tbody>
          {result.candidates.map((c) => (
            <tr key={c.rank} className="border-b border-border align-top last:border-b-0">
              <td className="p-2 font-mono">{c.rank}</td>
              <td className="p-2">{c.doc ?? "unknown"}{c.method ? <span className="block text-muted-foreground">{c.method}</span> : null}</td>
              <td className="p-2 font-mono">{formatMetric(c.score)}</td>
              <td className="p-2">
                {c.gold === null ? "n/a" : c.gold ? (
                  <span className="inline-flex items-center gap-1"><CheckCircle2 className="size-3" aria-hidden="true" /> Match</span>
                ) : (
                  <span className="inline-flex items-center gap-1"><XCircle className="size-3" aria-hidden="true" /> No match</span>
                )}
              </td>
              <td className="max-w-md p-2 text-muted-foreground">{c.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
