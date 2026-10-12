/**
 * T30: pure model for the eval dashboard. No database or React in here; `queries.ts` loads rows, this file turns them
 * into what the pages render and is covered by __tests__/eval-dashboard.test.tsx.
 *
 * Honesty rules encoded here (INV-11): a run is a measurement only when its stored meta says `measurement: true` and it is
 * not seeded; anything else carries a visible stamp and can never be the highlighted "best run".
 */

export const FAILURE_CATEGORIES = [
  "not retrieved",
  "ranked too low",
  "wrong version",
  "parse lost the content",
  "unsupported claim",
  "false abstention",
  "missed abstention",
  "unclassified",
] as const;
export type FailureCategory = (typeof FAILURE_CATEGORIES)[number];

export const RETRIEVAL_METRICS = ["recall@5", "mrr", "ndcg@10", "precision@5"] as const;
export const ANSWER_METRICS = ["correct", "citation_precision", "citation_recall", "groundedness"] as const;
export const PRIMARY_METRIC = { retrieval: "ndcg@10", answer: "correct" } as const;

export type RunKind = "retrieval" | "answer";

export interface RawRunRow {
  id: string;
  dataset_version: string;
  split: string;
  profile_id: string | null;
  config_hash: string | null;
  code_commit: string | null;
  meta: unknown;
  started_at: Date | string | null;
  finished_at: Date | string | null;
  case_count?: number | string | null;
}

export interface MetricStat {
  mean: number | null;
  lo: number | null;
  hi: number | null;
  n: number;
}

export interface EvalRun {
  id: string;
  kind: RunKind;
  datasetVersion: string;
  split: string;
  profileName: string;
  profileId: string | null;
  configHash: string | null;
  commit: string | null;
  modelIds: Record<string, string>;
  promptVersions: Record<string, string>;
  measurement: boolean;
  seeded: boolean;
  stamp: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  caseCount: number;
  overall: Record<string, MetricStat>;
  perCategory: Record<string, Record<string, MetricStat>>;
  abstention: { falseAbstainRate: number | null; abstentionRecall: number | null } | null;
}

export interface RawResultRow {
  case_id: string;
  metrics: unknown;
  ranking: unknown;
  latency_ms: number | null;
  cost: unknown;
}

export interface Candidate {
  rank: number;
  chunkId: string | null;
  doc: string | null;
  score: number | null;
  method: string | null;
  text: string;
  gold: boolean | null;
}

export interface EvalCaseResult {
  caseId: string;
  category: string;
  question: string | null;
  answerable: boolean | null;
  abstained: boolean | null;
  failure: FailureCategory | null;
  answer: string | null;
  claims: { text: string; status: string | null }[];
  traceId: string | null;
  latencyMs: number | null;
  metrics: Record<string, number>;
  candidates: Candidate[];
}

const iso = (v: unknown): string | null =>
  v === null || v === undefined ? null : v instanceof Date ? v.toISOString() : String(v);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const strMap = (v: unknown): Record<string, string> =>
  Object.fromEntries(Object.entries(obj(v)).filter(([, x]) => typeof x === "string")) as Record<string, string>;

function stat(v: unknown, n: number): MetricStat {
  // retrieval summary: mean is a number and ci95 sits in a sibling map; answer summary: {n, mean, ci95:[lo,hi]}
  const o = obj(v);
  const ci = Array.isArray(o.ci95) ? o.ci95 : [];
  return { mean: num(o.mean), lo: num(ci[0]), hi: num(ci[1]), n: num(o.n) ?? n };
}

function retrievalAgg(agg: unknown): Record<string, MetricStat> {
  const a = obj(agg);
  const n = num(a.n) ?? 0;
  const mean = obj(a.mean);
  const ci = obj(a.ci95);
  const out: Record<string, MetricStat> = {};
  for (const k of Object.keys(mean)) {
    const pair = Array.isArray(ci[k]) ? (ci[k] as unknown[]) : [];
    out[k] = { mean: num(mean[k]), lo: num(pair[0]), hi: num(pair[1]), n };
  }
  return out;
}

function answerAgg(block: unknown): Record<string, MetricStat> {
  const b = obj(block);
  const out: Record<string, MetricStat> = {};
  for (const k of ANSWER_METRICS) if (b[k] !== undefined) out[k] = stat(b[k], num(b.cases) ?? 0);
  return out;
}

/** Only meta.measurement === true on a non-seeded run is a measurement. Missing meta is not a measurement. */
export function isMeasurement(meta: unknown): boolean {
  const m = obj(meta);
  return m.measurement === true && m.seeded !== true;
}

export function normalizeRun(row: RawRunRow): EvalRun {
  const meta = obj(row.meta);
  const kind: RunKind = meta.kind === "answer" ? "answer" : "retrieval";
  const summary = obj(meta.summary);
  const measurement = isMeasurement(meta);
  const seeded = meta.seeded === true;
  const stamp =
    typeof meta.stamp === "string" && meta.stamp
      ? meta.stamp
      : measurement
        ? null
        : seeded
          ? "SEEDED TEST DATA - NOT A MEASUREMENT"
          : "FAKE - NOT A MEASUREMENT";
  let overall: Record<string, MetricStat> = {};
  const perCategory: Record<string, Record<string, MetricStat>> = {};
  let abstention: EvalRun["abstention"] = null;
  if (kind === "retrieval") {
    overall = retrievalAgg(summary.overall);
    for (const [c, a] of Object.entries(obj(summary.per_category))) perCategory[c] = retrievalAgg(a);
  } else {
    overall = answerAgg(summary.overall);
    for (const [c, b] of Object.entries(obj(summary.per_category))) perCategory[c] = answerAgg(b);
    const ab = obj(summary.abstention);
    abstention = { falseAbstainRate: num(ab.false_abstain_rate), abstentionRecall: num(ab.abstention_recall) };
  }
  return {
    id: row.id,
    kind,
    datasetVersion: row.dataset_version,
    split: row.split,
    profileName: typeof meta.profile_name === "string" ? meta.profile_name : "unnamed",
    profileId: row.profile_id,
    configHash: row.config_hash,
    commit: row.code_commit,
    modelIds: strMap(meta.model_ids),
    promptVersions: strMap(meta.prompt_versions),
    measurement,
    seeded,
    stamp,
    startedAt: iso(row.started_at),
    finishedAt: iso(row.finished_at),
    caseCount: Number(row.case_count ?? 0) || 0,
    overall,
    perCategory,
    abstention,
  };
}

export function normalizeResult(row: RawResultRow): EvalCaseResult {
  const raw = obj(row.metrics);
  const c = obj(raw._case);
  const metrics: Record<string, number> = {};
  for (const [k, v] of Object.entries(raw)) if (k !== "_case" && typeof v === "number") metrics[k] = v;
  const failure = typeof c.failure === "string" ? c.failure : null;
  const known = (FAILURE_CATEGORIES as readonly string[]).includes(failure ?? "");
  const candidates: Candidate[] = Array.isArray(row.ranking)
    ? (row.ranking as unknown[]).map((x, i) => {
        const o = obj(x);
        return {
          rank: num(o.rank) ?? i + 1,
          chunkId: typeof o.chunk_id === "string" ? o.chunk_id : null,
          doc: typeof o.doc === "string" ? o.doc : null,
          score: num(o.score),
          method: typeof o.method === "string" ? o.method : null,
          text: typeof o.text === "string" ? o.text : "",
          gold: typeof o.gold === "boolean" ? o.gold : null,
        };
      })
    : [];
  return {
    caseId: row.case_id,
    category: typeof c.category === "string" ? c.category : "uncategorised",
    question: typeof c.question === "string" ? c.question : null,
    answerable: typeof c.answerable === "boolean" ? c.answerable : null,
    abstained: typeof c.abstained === "boolean" ? c.abstained : null,
    failure: failure === null ? null : known ? (failure as FailureCategory) : "unclassified",
    answer: typeof c.answer === "string" ? c.answer : null,
    claims: Array.isArray(c.claims)
      ? (c.claims as unknown[]).map((x) => ({ text: String(obj(x).text ?? ""), status: typeof obj(x).status === "string" ? (obj(x).status as string) : null }))
      : [],
    traceId: typeof c.trace_id === "string" ? c.trace_id : null,
    latencyMs: row.latency_ms,
    metrics,
    candidates,
  };
}

export function metricNames(kind: RunKind): readonly string[] {
  return kind === "answer" ? ANSWER_METRICS : RETRIEVAL_METRICS;
}

/**
 * Ids of the best run per comparable group (same kind, dataset version and split), by the primary metric's mean.
 * Runs that are not measurements (fake, seeded, unknown) are never candidates. Ties keep the earliest-listed run.
 */
export function bestRunIds(runs: EvalRun[]): Set<string> {
  const best = new Map<string, EvalRun>();
  for (const r of runs) {
    if (!r.measurement) continue;
    const m = r.overall[PRIMARY_METRIC[r.kind]]?.mean;
    if (m === null || m === undefined) continue;
    const key = `${r.kind}|${r.datasetVersion}|${r.split}`;
    const cur = best.get(key);
    const curMean = cur ? cur.overall[PRIMARY_METRIC[cur.kind]]?.mean ?? -Infinity : -Infinity;
    if (!cur || m > curMean) best.set(key, r);
  }
  return new Set([...best.values()].map((r) => r.id));
}

export function failureCounts(results: EvalCaseResult[]): { category: FailureCategory; count: number }[] {
  const counts = new Map<FailureCategory, number>();
  for (const r of results) if (r.failure) counts.set(r.failure, (counts.get(r.failure) ?? 0) + 1);
  return FAILURE_CATEGORIES.filter((c) => counts.has(c)).map((c) => ({ category: c, count: counts.get(c)! }));
}

// ----------------------------------------------------------------------------------------------- compare

function mulberry32(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const BOOTSTRAP_RESAMPLES = 2000;
export const BOOTSTRAP_SEED = 20260101;

export interface PairedDelta {
  n: number;
  meanDiff: number | null;
  lo: number | null;
  hi: number | null;
  significant: boolean;
}

/** Paired percentile bootstrap of mean(b - a) over shared case ids (seeded; 2,000 resamples). */
export function pairedBootstrap(diffs: number[], resamples = BOOTSTRAP_RESAMPLES, seed = BOOTSTRAP_SEED): PairedDelta {
  const n = diffs.length;
  if (n === 0) return { n: 0, meanDiff: null, lo: null, hi: null, significant: false };
  const rng = mulberry32(seed);
  const means: number[] = [];
  for (let r = 0; r < resamples; r++) {
    let s = 0;
    for (let i = 0; i < n; i++) s += diffs[Math.floor(rng() * n)];
    means.push(s / n);
  }
  means.sort((x, y) => x - y);
  const lo = means[Math.floor(0.025 * resamples)];
  const hi = means[Math.min(resamples - 1, Math.floor(0.975 * resamples))];
  const meanDiff = diffs.reduce((s, d) => s + d, 0) / n;
  return { n, meanDiff, lo, hi, significant: lo > 0 || hi < 0 };
}

export interface QuestionDelta {
  caseId: string;
  category: string;
  question: string | null;
  a: number | null;
  b: number | null;
  delta: number;
  failureA: FailureCategory | null;
  failureB: FailureCategory | null;
}

export interface RunComparison {
  comparable: boolean;
  /** Why the runs cannot be paired; null when comparable. */
  reason: string | null;
  /** True when either run is not a measurement: deltas are plumbing checks, not evidence. */
  includesNonMeasurement: boolean;
  metric: string;
  overall: { metric: string; a: number | null; b: number | null; paired: PairedDelta }[];
  perCategory: { category: string; n: number; metric: string; a: number | null; b: number | null; delta: number | null }[];
  questions: QuestionDelta[];
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((s, x) => s + x, 0) / xs.length : null);

export function compareRuns(a: EvalRun, b: EvalRun, ra: EvalCaseResult[], rb: EvalCaseResult[]): RunComparison {
  const includesNonMeasurement = !a.measurement || !b.measurement;
  const metric = PRIMARY_METRIC[a.kind];
  const base = { includesNonMeasurement, metric, overall: [], perCategory: [], questions: [] };
  if (a.id === b.id) return { ...base, comparable: false, reason: "Choose two different runs." };
  if (a.kind !== b.kind) return { ...base, comparable: false, reason: `A is a ${a.kind} run and B is a ${b.kind} run: they measure different things.` };
  if (a.datasetVersion !== b.datasetVersion || a.split !== b.split)
    return {
      ...base,
      comparable: false,
      reason: `A used ${a.datasetVersion}/${a.split} and B used ${b.datasetVersion}/${b.split}: the same questions are needed for a paired comparison.`,
    };
  const mb = new Map(rb.map((r) => [r.caseId, r]));
  const shared = ra.filter((r) => mb.has(r.caseId)).map((r) => [r, mb.get(r.caseId)!] as const);
  const overall = metricNames(a.kind).map((m) => {
    const diffs = shared.filter(([x, y]) => m in x.metrics && m in y.metrics).map(([x, y]) => y.metrics[m] - x.metrics[m]);
    return { metric: m, a: a.overall[m]?.mean ?? null, b: b.overall[m]?.mean ?? null, paired: pairedBootstrap(diffs) };
  });
  const cats = [...new Set(shared.map(([x]) => x.category))].sort();
  const perCategory = cats.map((category) => {
    const rows = shared.filter(([x, y]) => x.category === category && metric in x.metrics && metric in y.metrics);
    const am = mean(rows.map(([x]) => x.metrics[metric]));
    const bm = mean(rows.map(([, y]) => y.metrics[metric]));
    return { category, n: rows.length, metric, a: am, b: bm, delta: am === null || bm === null ? null : bm - am };
  });
  const questions: QuestionDelta[] = shared
    .map(([x, y]) => {
      const va = x.metrics[metric] ?? null;
      const vb = y.metrics[metric] ?? null;
      return {
        caseId: x.caseId, category: x.category, question: x.question ?? y.question, a: va, b: vb,
        delta: va === null || vb === null ? 0 : vb - va, failureA: x.failure, failureB: y.failure,
      };
    })
    .filter((q) => q.delta !== 0 || q.failureA !== q.failureB)
    .sort((p, q) => Math.abs(q.delta) - Math.abs(p.delta) || p.caseId.localeCompare(q.caseId));
  return { comparable: true, reason: null, includesNonMeasurement, metric, overall, perCategory, questions };
}

export function formatMetric(v: number | null | undefined, digits = 3): string {
  return v === null || v === undefined || Number.isNaN(v) ? "n/a" : v.toFixed(digits);
}
export function formatInterval(s: MetricStat | undefined): string {
  if (!s || s.mean === null) return "n/a";
  return s.lo === null || s.hi === null
    ? formatMetric(s.mean)
    : `${formatMetric(s.mean)} [${formatMetric(s.lo)}, ${formatMetric(s.hi)}]`;
}
export function formatDelta(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "n/a";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;
}

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
