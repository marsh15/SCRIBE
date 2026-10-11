"use client";

import { useId, useRef, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  COMPARE_MAX_PROFILES,
  COMPARE_MIN_PROFILES,
  COMPARE_PROFILES,
  degradedLabel,
  describeMovement,
  formatCost,
  orderedStages,
  type AlignmentRow,
  type CompareProfileResult,
  type CompareResponse,
  type Movement,
} from "@/lib/compare";

const DEFAULT_SELECTION = ["dense-only", "hybrid-rrf", "hybrid-rrf-rerank"];
const label = (name: string) => COMPARE_PROFILES.find((p) => p.name === name)?.label ?? name;

type Failure = { message: string };

async function readFailure(res: Response): Promise<Failure> {
  const j = (await res.json().catch(() => null)) as { error?: { code?: string; message?: string } } | null;
  if (res.status === 401) return { message: "Sign in to compare profiles." };
  return { message: j?.error?.message ?? "The comparison could not be completed. Please retry." };
}

/** Movement cue: border style + arrow + words, so no state relies on colour alone. */
function movementClass(m: Movement | undefined, isBaseline: boolean): string {
  if (isBaseline || !m) return "border-l-4 border-l-transparent";
  switch (m.direction) {
    case "up":
      return "border-l-4 border-solid border-l-rag bg-rag/10";
    case "down":
      return "border-l-4 border-solid border-l-foreground/70 bg-muted";
    case "new":
      return "border-l-4 border-double border-l-rag bg-rag/5";
    case "dropped":
      return "border-l-4 border-dashed border-l-foreground/50 bg-muted/60";
    default:
      return "border-l-4 border-l-transparent";
  }
}

function Score({ name, rank, score, showScores }: { name: string; rank: number | null; score: number | null; showScores: boolean }) {
  if (rank === null) return null;
  return (
    <span className="whitespace-nowrap">
      {name} #{rank}
      {showScores && score !== null ? ` (${score.toFixed(4)})` : ""}
    </span>
  );
}

function Cell({
  row, profile, baseline, result, showScores, limit,
}: { row: AlignmentRow; profile: string; baseline: string; result: CompareProfileResult | undefined; showScores: boolean; limit: number }) {
  const rank = row.ranks[profile];
  const isBaseline = profile === baseline;
  const m = describeMovement(row.movement[profile], isBaseline);
  const detail = result?.results.find((r) => r.chunk_id === row.chunk_id);
  return (
    <td className={`align-top p-3 text-xs ${movementClass(row.movement[profile], isBaseline)}`}>
      {rank === null ? (
        <p className="text-muted-foreground">
          Not in top {limit}
          {!isBaseline && m.text !== "not returned" ? (
            <span className="block font-medium text-foreground">
              <span aria-hidden="true">{m.arrow} </span>
              {m.text}
            </span>
          ) : null}
        </p>
      ) : (
        <>
          <p className="font-mono text-sm font-semibold">Rank {rank}</p>
          <p className="mt-0.5 font-medium">
            {m.arrow ? <span aria-hidden="true">{m.arrow} </span> : null}
            {m.text}
          </p>
          {detail ? (
            <p className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-muted-foreground">
              <Score name="dense" rank={detail.dense_rank} score={detail.dense_score} showScores={showScores} />
              <Score name="lexical" rank={detail.lexical_rank} score={detail.lexical_score} showScores={showScores} />
              <Score name="fused" rank={detail.fusion_rank} score={detail.fusion_score} showScores={showScores} />
              <Score name="rerank" rank={detail.rerank_rank} score={detail.rerank_score} showScores={showScores} />
              {detail.in_context ? <span>in context</span> : null}
            </p>
          ) : null}
        </>
      )}
    </td>
  );
}

function ProfileCard({ p, isBaseline }: { p: CompareProfileResult; isBaseline: boolean }) {
  const stages = orderedStages(p.stage_ms);
  const total = p.stage_ms.total ?? p.stage_ms.request_total;
  return (
    <section aria-labelledby={`card-${p.profile}`} className="rounded-md border border-border bg-background p-4">
      <h3 id={`card-${p.profile}`} className="text-sm font-semibold">
        {label(p.profile)}
        {isBaseline ? <span className="ml-2 text-xs font-normal text-muted-foreground">(baseline)</span> : null}
      </h3>
      <p className="mt-1 font-mono text-[11px] text-muted-foreground">
        {p.profile} · config {p.config_hash.slice(0, 10)}
      </p>
      {p.degraded.length > 0 ? (
        <ul className="mt-2 space-y-1" aria-label={`Degraded: ${label(p.profile)}`}>
          {p.degraded.map((d) => (
            <li key={d} role="status" className="flex items-start gap-1.5 rounded-sm border border-dashed border-foreground/60 px-2 py-1 text-xs font-medium">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <span>Degraded: {degradedLabel(d)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
        <dt className="text-muted-foreground">Returned</dt>
        <dd>{p.results.length} results</dd>
        {total !== undefined ? (
          <>
            <dt className="text-muted-foreground">Retrieval total</dt>
            <dd>{total} ms</dd>
          </>
        ) : null}
        {stages.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground">{k.replace(/_/g, " ")}</dt>
            <dd>{v} ms</dd>
          </div>
        ))}
        <dt className="text-muted-foreground">Rerank</dt>
        <dd>
          {p.cost.rerank_documents} docs, {p.cost.rerank_tokens} tokens
        </dd>
        <dt className="text-muted-foreground">Cost</dt>
        <dd>
          {formatCost(p.cost)} <span className="text-muted-foreground">({p.cost.note})</span>
        </dd>
      </dl>
    </section>
  );
}

export function CompareLab() {
  const queryId = useId();
  const baselineId = useId();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>(DEFAULT_SELECTION);
  const [baseline, setBaseline] = useState<string>(DEFAULT_SELECTION[0]);
  const [showScores, setShowScores] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [data, setData] = useState<CompareResponse | null>(null);
  const requestRef = useRef(0);

  const ordered = COMPARE_PROFILES.map((p) => p.name).filter((n) => selected.includes(n));
  const baselineName = ordered.includes(baseline) ? baseline : ordered[0];
  const countOk = ordered.length >= COMPARE_MIN_PROFILES && ordered.length <= COMPARE_MAX_PROFILES;
  const canRun = query.trim().length > 0 && countOk && !busy;

  const toggle = (name: string) =>
    setSelected((cur) => (cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name]));

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!canRun) return;
    const mine = ++requestRef.current;
    setBusy(true);
    setFailure(null);
    const profiles = [baselineName, ...ordered.filter((n) => n !== baselineName)];
    try {
      const res = await fetch("/api/compare", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ query: query.trim(), profiles, limit: 10 }),
      });
      if (mine !== requestRef.current) return;
      if (!res.ok) {
        setData(null);
        setFailure(await readFailure(res));
      } else {
        setData((await res.json()) as CompareResponse);
      }
    } catch {
      if (mine === requestRef.current) {
        setData(null);
        setFailure({ message: "The comparison could not be completed. Check your connection and retry." });
      }
    } finally {
      if (mine === requestRef.current) setBusy(false);
    }
  }

  const byProfile = new Map((data?.profile_results ?? []).map((p) => [p.profile, p]));
  const columns = data?.profiles ?? [];

  return (
    <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6">
      <header>
        <h1 className="text-xl font-semibold">Retrieval lab</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Run one query through two to five retrieval profiles at the same moment. All profiles read one database
          snapshot and share one query embedding, so differences come from the profile, not from timing.
        </p>
      </header>

      <form onSubmit={run} className="space-y-4" aria-describedby="compare-help">
        <div>
          <label htmlFor={queryId} className="mb-1 block text-sm font-medium">
            Query
          </label>
          <input
            id={queryId}
            type="text"
            value={query}
            maxLength={2000}
            onChange={(e) => setQuery(e.target.value)}
            className="h-11 w-full rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
            placeholder="Ask something your sources can answer"
            autoComplete="off"
          />
        </div>

        <fieldset>
          <legend className="mb-1 text-sm font-medium">
            Profiles (choose {COMPARE_MIN_PROFILES} to {COMPARE_MAX_PROFILES})
          </legend>
          <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {COMPARE_PROFILES.map((p) => {
              const checked = selected.includes(p.name);
              const atMax = !checked && selected.length >= COMPARE_MAX_PROFILES;
              return (
                <li key={p.name}>
                  <label className={`flex min-h-11 cursor-pointer items-start gap-3 rounded-md border border-border p-3 text-sm has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50 ${checked ? "bg-muted" : ""} ${atMax ? "opacity-60" : ""}`}>
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4"
                      checked={checked}
                      disabled={atMax}
                      onChange={() => toggle(p.name)}
                    />
                    <span>
                      <span className="block font-medium">{p.label}</span>
                      <span className="block text-xs text-muted-foreground">{p.summary}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          <p id="compare-help" className="mt-2 text-xs text-muted-foreground" aria-live="polite">
            {ordered.length} selected.{" "}
            {countOk ? "" : `Select between ${COMPARE_MIN_PROFILES} and ${COMPARE_MAX_PROFILES} profiles to compare.`}
          </p>
        </fieldset>

        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor={baselineId} className="mb-1 block text-sm font-medium">
              Measure movement against
            </label>
            <select
              id={baselineId}
              value={baselineName ?? ""}
              onChange={(e) => setBaseline(e.target.value)}
              className="h-11 rounded-md border border-input bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
              disabled={ordered.length === 0}
            >
              {ordered.map((n) => (
                <option key={n} value={n}>
                  {label(n)}
                </option>
              ))}
            </select>
          </div>
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input type="checkbox" className="size-4" checked={showScores} onChange={(e) => setShowScores(e.target.checked)} />
            Show raw scores
          </label>
          <Button type="submit" disabled={!canRun} className="h-11 px-6">
            {busy ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : null}
            {busy ? "Comparing" : "Compare profiles"}
          </Button>
        </div>
      </form>

      <div aria-live="polite" role="status" className="min-h-5 text-sm">
        {busy ? "Comparing profiles…" : null}
        {failure ? <p className="font-medium">{failure.message}</p> : null}
        {data && !busy ? (
          <p>
            Compared {data.profiles.length} profiles in one snapshot. Query embedded {data.shared.embedding.calls} time
            {data.shared.embedding.calls === 1 ? "" : "s"}. Total {data.request_total_ms} ms. Estimated cost{" "}
            {formatCost(data.cost)} ({data.cost.note}).
            {data.shared.embedding.is_fake ? " Fake embedder in use: ranks are not meaningful." : ""}
          </p>
        ) : null}
      </div>

      {data ? (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {columns.map((n) => {
              const p = byProfile.get(n);
              return p ? <ProfileCard key={n} p={p} isBaseline={n === data.alignment.baseline} /> : null;
            })}
          </div>

          <section aria-labelledby="aligned-heading" className="space-y-2">
            <h2 id="aligned-heading" className="text-base font-semibold">
              Aligned rankings
            </h2>
            <p className="text-xs text-muted-foreground">
              Each row is one passage. Each column shows that profile&apos;s rank and how far the passage moved compared with the
              baseline ({label(data.alignment.baseline)}). Ranks cover the top {data.limit} of each profile; &quot;Not in top&quot; means
              outside that list, not that it was never retrieved. Scores from different rankers are shown separately and never added.
            </p>
            {data.alignment.rows.length === 0 ? (
              <p className="rounded-md border border-border p-4 text-sm">No profile returned any passage for this query.</p>
            ) : (
              <div className="overflow-x-auto rounded-md border border-border" tabIndex={0} role="region" aria-label="Aligned rankings, scrollable">
                <table className="w-full min-w-[720px] border-collapse text-left">
                  <caption className="sr-only">
                    Passages returned by {columns.map(label).join(", ")}, with rank and movement against the baseline profile.
                  </caption>
                  <thead>
                    <tr className="border-b border-border bg-muted/50 text-xs">
                      <th scope="col" className="p-3 font-semibold">Passage</th>
                      {columns.map((n) => (
                        <th key={n} scope="col" className="p-3 font-semibold">
                          {label(n)}
                          {n === data.alignment.baseline ? <span className="block font-normal text-muted-foreground">baseline</span> : null}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {data.alignment.rows.map((row) => (
                      <tr key={row.chunk_id} className="border-b border-border last:border-b-0">
                        <th scope="row" className="max-w-xs p-3 align-top text-xs font-normal">
                          <span className="block font-semibold">{row.doc_name}</span>
                          {row.heading_text ? <span className="block text-muted-foreground">{row.heading_text}</span> : null}
                          <span className="mt-1 block text-muted-foreground">{row.snippet}</span>
                        </th>
                        {columns.map((n) => (
                          <Cell
                            key={n}
                            row={row}
                            profile={n}
                            baseline={data.alignment.baseline}
                            result={byProfile.get(n)}
                            showScores={showScores}
                            limit={data.limit}
                          />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section aria-labelledby="summary-heading" className="space-y-2">
            <h2 id="summary-heading" className="text-base font-semibold">
              Movement summary
            </h2>
            <ul className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
              {columns.map((n) => {
                const s = data.alignment.summary[n];
                if (!s) return null;
                const isBase = n === data.alignment.baseline;
                return (
                  <li key={n} className="rounded-md border border-border p-3">
                    <span className="font-semibold">{label(n)}</span>
                    {isBase ? (
                      <span className="block text-muted-foreground">Baseline: {s.returned} passages</span>
                    ) : (
                      <span className="block text-muted-foreground">
                        {s.up} up, {s.down} down, {s.same} same rank, {s.new} new, {s.dropped} dropped. Overlap with baseline:{" "}
                        {s.overlap_with_baseline} passages (Jaccard {s.jaccard_with_baseline.toFixed(2)}).
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        </>
      ) : null}
    </div>
  );
}
