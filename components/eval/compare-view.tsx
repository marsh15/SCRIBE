import Link from "next/link";
import { AlertCircle } from "lucide-react";
import { RunStamp, shortHash, shortId } from "@/components/eval/badges";
import { formatDelta, formatMetric, type EvalRun, type RunComparison } from "@/lib/eval/model";

export function CompareForm({ runs, a, b }: { runs: EvalRun[]; a?: string; b?: string }) {
  const opt = (r: EvalRun) => (
    <option key={r.id} value={r.id}>
      {r.profileName} · {r.split} · {shortId(r.id)}{r.measurement ? "" : " · NOT A MEASUREMENT"}
    </option>
  );
  return (
    <form method="get" action="/eval/compare" className="flex flex-wrap items-end gap-4">
      {(["a", "b"] as const).map((k) => (
        <div key={k}>
          <label htmlFor={`run-${k}`} className="mb-1 block text-sm font-medium">Run {k.toUpperCase()}{k === "a" ? " (baseline)" : ""}</label>
          <select
            id={`run-${k}`}
            name={k}
            defaultValue={(k === "a" ? a : b) ?? ""}
            className="h-11 max-w-xs rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <option value="">Choose a run</option>
            {runs.map(opt)}
          </select>
        </div>
      ))}
      <button
        type="submit"
        className="inline-flex h-11 items-center rounded-md border border-border bg-primary px-6 text-sm font-medium text-primary-foreground outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        Compare runs
      </button>
    </form>
  );
}

export function CompareResult({ a, b, cmp }: { a: EvalRun; b: EvalRun; cmp: RunComparison }) {
  return (
    <div className="space-y-6">
      <ul className="grid gap-3 sm:grid-cols-2">
        {[["A", a], ["B", b]].map(([label, r]) => {
          const run = r as EvalRun;
          return (
            <li key={label as string} className="rounded-md border border-border p-3 text-sm">
              <span className="font-mono text-xs text-muted-foreground">{label as string}</span>{" "}
              <Link href={`/eval/${run.id}`} className="font-medium underline-offset-2 hover:underline">{run.profileName}</Link>
              <span className="block text-xs text-muted-foreground">
                {run.datasetVersion} / {run.split} · config {shortHash(run.configHash)}
              </span>
              <span className="mt-1 block"><RunStamp run={run} /></span>
            </li>
          );
        })}
      </ul>
      {!cmp.comparable ? (
        <p role="alert" className="flex items-start gap-2 rounded-md border border-border p-3 text-sm">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          {cmp.reason}
        </p>
      ) : (
        <>
          {cmp.includesNonMeasurement ? (
            <p role="note" className="flex items-start gap-2 rounded-md border border-destructive bg-destructive/10 p-3 text-sm">
              <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" />
              At least one of these runs is not a measurement. The deltas below check the page and the pipeline plumbing; they say nothing about quality.
            </p>
          ) : null}
          <section aria-labelledby="ov-heading" className="space-y-2">
            <h2 id="ov-heading" className="text-base font-semibold">Overall deltas (B minus A)</h2>
            <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Overall deltas, scrollable">
              <table className="w-full min-w-[640px] border-collapse text-left text-sm">
                <caption className="sr-only">Mean of each metric in run A and run B, and the paired bootstrap difference with its 95 percent interval.</caption>
                <thead>
                  <tr className="border-b border-border bg-muted/50 text-xs">
                    <th scope="col" className="p-3 font-semibold">Metric</th>
                    <th scope="col" className="p-3 font-semibold">A</th>
                    <th scope="col" className="p-3 font-semibold">B</th>
                    <th scope="col" className="p-3 font-semibold">Paired cases</th>
                    <th scope="col" className="p-3 font-semibold">Mean difference [95% CI]</th>
                    <th scope="col" className="p-3 font-semibold">Interval excludes 0</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.overall.map((o) => (
                    <tr key={o.metric} className="border-b border-border last:border-b-0">
                      <th scope="row" className="p-3 text-left font-normal">{o.metric}</th>
                      <td className="p-3 font-mono text-xs">{formatMetric(o.a)}</td>
                      <td className="p-3 font-mono text-xs">{formatMetric(o.b)}</td>
                      <td className="p-3">{o.paired.n}</td>
                      <td className="p-3 font-mono text-xs">
                        {formatDelta(o.paired.meanDiff)} [{formatDelta(o.paired.lo)}, {formatDelta(o.paired.hi)}]
                      </td>
                      <td className="p-3">{o.paired.n === 0 ? "n/a" : o.paired.significant ? "Yes" : "No"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted-foreground">
              Paired percentile bootstrap over shared questions, seeded, 2,000 resamples (a TypeScript implementation; the
              scribe-eval compare command uses 10,000 resamples so the last digits can differ).
            </p>
          </section>

          <section aria-labelledby="pc-heading" className="space-y-2">
            <h2 id="pc-heading" className="text-base font-semibold">Per category ({cmp.metric})</h2>
            <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Per-category deltas, scrollable">
              <table className="w-full min-w-[520px] border-collapse text-left text-sm">
                <caption className="sr-only">Mean {cmp.metric} for each category in runs A and B and the plain difference. No interval: categories hold few questions.</caption>
                <thead>
                  <tr className="border-b border-border bg-muted/50 text-xs">
                    <th scope="col" className="p-3 font-semibold">Category</th>
                    <th scope="col" className="p-3 font-semibold">Cases</th>
                    <th scope="col" className="p-3 font-semibold">A</th>
                    <th scope="col" className="p-3 font-semibold">B</th>
                    <th scope="col" className="p-3 font-semibold">B minus A</th>
                  </tr>
                </thead>
                <tbody>
                  {cmp.perCategory.map((c) => (
                    <tr key={c.category} className="border-b border-border last:border-b-0">
                      <th scope="row" className="p-3 text-left font-normal">{c.category}</th>
                      <td className="p-3">{c.n}</td>
                      <td className="p-3 font-mono text-xs">{formatMetric(c.a)}</td>
                      <td className="p-3 font-mono text-xs">{formatMetric(c.b)}</td>
                      <td className="p-3 font-mono text-xs">{formatDelta(c.delta)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section aria-labelledby="pq-heading" className="space-y-2">
            <h2 id="pq-heading" className="text-base font-semibold">Questions that changed ({cmp.questions.length})</h2>
            {cmp.questions.length === 0 ? (
              <p className="text-sm">No question differs between these runs on {cmp.metric}.</p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Per-question deltas, scrollable">
                <table className="w-full min-w-[720px] border-collapse text-left text-sm">
                  <caption className="sr-only">Questions where {cmp.metric} or the failure category differs between run A and run B, largest change first.</caption>
                  <thead>
                    <tr className="border-b border-border bg-muted/50 text-xs">
                      <th scope="col" className="p-3 font-semibold">Question</th>
                      <th scope="col" className="p-3 font-semibold">A</th>
                      <th scope="col" className="p-3 font-semibold">B</th>
                      <th scope="col" className="p-3 font-semibold">B minus A</th>
                      <th scope="col" className="p-3 font-semibold">Failure A to B</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cmp.questions.map((q) => (
                      <tr key={q.caseId} className="border-b border-border align-top last:border-b-0">
                        <th scope="row" className="max-w-sm p-3 text-left font-normal">
                          <span className="font-mono text-xs">{q.caseId}</span> <span className="text-xs text-muted-foreground">{q.category}</span>
                          <span className="block text-xs">{q.question ?? ""}</span>
                        </th>
                        <td className="p-3 font-mono text-xs">{formatMetric(q.a)}</td>
                        <td className="p-3 font-mono text-xs">{formatMetric(q.b)}</td>
                        <td className="p-3 font-mono text-xs">{formatDelta(q.delta)}</td>
                        <td className="p-3 text-xs">{q.failureA ?? "passes"} to {q.failureB ?? "passes"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
