import Link from "next/link";
import { BestBadge, RunStamp, formatWhen, modelList, shortCommit, shortHash, shortId } from "@/components/eval/badges";
import { bestRunIds, formatInterval, metricNames, type EvalRun } from "@/lib/eval/model";

export function RunList({ runs, compareA }: { runs: EvalRun[]; compareA?: string }) {
  const best = bestRunIds(runs);
  if (runs.length === 0) {
    return (
      <p className="rounded-md border border-border p-4 text-sm">
        No eval runs have been published yet. Publish one with{" "}
        <code className="font-mono text-xs">scribe-eval publish-run &lt;run dir&gt; --database-url …</code>.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Eval runs, scrollable">
      <table className="w-full min-w-[960px] border-collapse text-left text-sm">
        <caption className="sr-only">
          Eval runs, newest first, with dataset version, split, profile, configuration hash, commit, model ids, status and
          key metrics with 95 percent confidence intervals.
        </caption>
        <thead>
          <tr className="border-b border-border bg-muted/50 text-xs">
            <th scope="col" className="p-3 font-semibold">Run</th>
            <th scope="col" className="p-3 font-semibold">Dataset / split</th>
            <th scope="col" className="p-3 font-semibold">Profile and build</th>
            <th scope="col" className="p-3 font-semibold">Models</th>
            <th scope="col" className="p-3 font-semibold">Key metrics (95% CI)</th>
            <th scope="col" className="p-3 font-semibold">Compare</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-border align-top last:border-b-0" data-run-id={r.id}>
              <th scope="row" className="p-3 text-left font-normal">
                <Link href={`/eval/${r.id}`} className="font-medium underline-offset-2 hover:underline">
                  {r.profileName}
                </Link>
                <span className="block font-mono text-xs text-muted-foreground">{shortId(r.id)} · {r.kind}</span>
                <span className="mt-1 flex flex-wrap gap-1">
                  <RunStamp run={r} />
                  {best.has(r.id) ? <BestBadge /> : null}
                </span>
              </th>
              <td className="p-3">
                {r.datasetVersion}
                <span className="block text-xs text-muted-foreground">{r.split} split · {r.caseCount} cases</span>
                <span className="block text-xs text-muted-foreground">{formatWhen(r.startedAt)}</span>
              </td>
              <td className="p-3 font-mono text-xs">
                <span className="block">config {shortHash(r.configHash)}</span>
                <span className="block text-muted-foreground">commit {shortCommit(r.commit)}</span>
              </td>
              <td className="max-w-[16rem] p-3 text-xs text-muted-foreground">{modelList(r.modelIds)}</td>
              <td className="p-3">
                <ul className="space-y-0.5 text-xs">
                  {metricNames(r.kind).map((m) => (
                    <li key={m}>
                      <span className="text-muted-foreground">{m}</span>{" "}
                      <span className="font-mono">{formatInterval(r.overall[m])}</span>
                    </li>
                  ))}
                </ul>
              </td>
              <td className="p-3 text-xs">
                {compareA && compareA !== r.id ? (
                  <Link href={`/eval/compare?a=${compareA}&b=${r.id}`} className="inline-flex min-h-11 items-center underline">
                    Compare with {shortId(compareA)}
                  </Link>
                ) : (
                  <Link href={`/eval?a=${r.id}`} className="inline-flex min-h-11 items-center underline">
                    {compareA === r.id ? "Selected as A" : "Set as A"}
                  </Link>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
