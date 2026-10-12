import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({ sql: vi.fn(), userId: vi.fn(), notFound: vi.fn(() => { throw new Error("NEXT_NOT_FOUND"); }) }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => m.sql(strings.join("?"), values),
  db: {},
}));
vi.mock("@/lib/auth", () => ({ getUserId: m.userId }));
vi.mock("next/navigation", () => ({ notFound: m.notFound }));

import EvalLayout from "@/app/eval/layout";
import EvalRunPage from "@/app/eval/[runId]/page";
import { CompareResult } from "@/components/eval/compare-view";
import { RunList } from "@/components/eval/run-list";
import { CategoryTable, FailureDrilldown, FailureSummary, RunHeader } from "@/components/eval/run-detail";
import {
  bestRunIds, compareRuns, failureCounts, normalizeResult, normalizeRun, pairedBootstrap,
  type EvalCaseResult, type EvalRun, type RawResultRow, type RawRunRow,
} from "@/lib/eval/model";
import { canViewEval, getEvalRun, listEvalResults, listEvalRuns } from "@/lib/eval/queries";

const ID_A = "11111111-1111-4111-8111-111111111111";
const ID_B = "22222222-2222-4222-8222-222222222222";
const ID_FAKE = "33333333-3333-4333-8333-333333333333";

const agg = (n: number, mean: Record<string, number>) => ({
  n, mean, ci95: Object.fromEntries(Object.entries(mean).map(([k, v]) => [k, [v - 0.1, v + 0.1]])),
});
function rawRun(id: string, o: { ndcg: number; measurement?: boolean; seeded?: boolean; split?: string; kind?: string; stamp?: string | null; profile?: string }): RawRunRow {
  const mean = { "recall@5": o.ndcg, mrr: o.ndcg, "ndcg@10": o.ndcg, "precision@5": 0.2 };
  return {
    id, dataset_version: "golden-v1", split: o.split ?? "dev", profile_id: null, config_hash: "abcdef0123456789", code_commit: "c8612a3ffff",
    started_at: new Date("2026-01-02T03:04:05Z"), finished_at: null, case_count: 4,
    meta: {
      workspace_kind: "eval", kind: o.kind ?? "retrieval", profile_name: o.profile ?? `profile-${id.slice(0, 2)}`,
      model_ids: { embed: "gemini-embedding-2" }, prompt_versions: {}, measurement: o.measurement ?? true, seeded: o.seeded ?? false,
      stamp: o.stamp ?? null, summary: { overall: agg(4, mean), per_category: { "single-fact": agg(2, mean), aggregation: agg(2, mean) } },
    },
  };
}
const res = (id: string, cat: string, ndcg: number, failure: string | null, extra: Record<string, unknown> = {}): RawResultRow => ({
  case_id: id, latency_ms: 10, cost: null,
  metrics: { "ndcg@10": ndcg, "recall@5": ndcg, mrr: ndcg, "precision@5": 0, _case: { category: cat, question: `Question ${id}?`, answerable: true, failure, ...extra } },
  ranking: [{ rank: 1, chunk_id: "c1", doc: "a.md", score: 0.5, method: "dense", text: "passage text", gold: false }],
});

const runA = normalizeRun(rawRun(ID_A, { ndcg: 0.5 }));
const runB = normalizeRun(rawRun(ID_B, { ndcg: 0.7 }));
const runFake = normalizeRun(rawRun(ID_FAKE, { ndcg: 0.99, measurement: false, stamp: "FAKE - NOT A MEASUREMENT" }));
const resultsA = [res("q1", "single-fact", 0, "not retrieved"), res("q2", "single-fact", 1, null), res("q3", "aggregation", 0.5, "ranked too low"), res("q4", "aggregation", 1, null)].map(normalizeResult);
const resultsB = [res("q1", "single-fact", 1, null), res("q2", "single-fact", 1, null), res("q3", "aggregation", 0.5, "ranked too low"), res("q4", "aggregation", 0, "weird made-up")].map(normalizeResult);

beforeEach(() => vi.clearAllMocks());

describe("model", () => {
  it("normalises run rows, marks measurement honestly and stamps non-measurements", () => {
    expect(runA.measurement).toBe(true);
    expect(runA.stamp).toBeNull();
    expect(runFake.measurement).toBe(false);
    expect(runFake.stamp).toBe("FAKE - NOT A MEASUREMENT");
    expect(runA.overall["ndcg@10"]).toMatchObject({ mean: 0.5, lo: 0.4, hi: 0.6 });
    // missing meta is never a measurement
    const bare = normalizeRun({ ...rawRun(ID_A, { ndcg: 1 }), meta: null });
    expect(bare.measurement).toBe(false);
    expect(bare.stamp).toContain("NOT A MEASUREMENT");
    const seeded = normalizeRun(rawRun(ID_A, { ndcg: 1, seeded: true, measurement: true }));
    expect(seeded.measurement).toBe(false);
    expect(seeded.stamp).toContain("SEEDED");
  });

  it("never highlights a fake, seeded or unknown run as best, even with the top score", () => {
    const seeded = normalizeRun(rawRun("44444444-4444-4444-8444-444444444444", { ndcg: 0.95, seeded: true }));
    expect([...bestRunIds([runA, runB, runFake, seeded])]).toEqual([ID_B]);
    expect(bestRunIds([runFake, seeded]).size).toBe(0);
    // different split is a different group
    const test = normalizeRun(rawRun("55555555-5555-4555-8555-555555555555", { ndcg: 0.1, split: "validation" }));
    expect(bestRunIds([runA, runB, test]).has(test.id)).toBe(true);
  });

  it("maps an unknown stored failure to unclassified and counts failures", () => {
    expect(resultsB[3].failure).toBe("unclassified");
    expect(failureCounts(resultsA)).toEqual([{ category: "not retrieved", count: 1 }, { category: "ranked too low", count: 1 }]);
  });

  it("paired bootstrap is deterministic and brackets the mean difference", () => {
    const p = pairedBootstrap([0.1, 0.2, 0.15, 0.3, 0.25, 0.2]);
    expect(p).toEqual(pairedBootstrap([0.1, 0.2, 0.15, 0.3, 0.25, 0.2]));
    expect(p.lo!).toBeLessThanOrEqual(p.meanDiff!);
    expect(p.hi!).toBeGreaterThanOrEqual(p.meanDiff!);
    expect(p.significant).toBe(true);
    expect(pairedBootstrap([]).n).toBe(0);
    expect(pairedBootstrap([0, 0, 0]).significant).toBe(false);
  });

  it("compares overall, per category and per question; refuses unpaired runs", () => {
    const cmp = compareRuns(runA, runB, resultsA, resultsB);
    expect(cmp.comparable).toBe(true);
    const ndcg = cmp.overall.find((o) => o.metric === "ndcg@10")!;
    expect(ndcg.paired.n).toBe(4);
    expect(ndcg.paired.meanDiff).toBeCloseTo((1 + 0 + 0 - 1) / 4);
    expect(cmp.perCategory.find((c) => c.category === "single-fact")).toMatchObject({ n: 2, delta: 0.5 });
    expect(cmp.questions.map((q) => q.caseId)).toEqual(["q1", "q4"]);
    expect(cmp.questions[0]).toMatchObject({ delta: 1, failureA: "not retrieved", failureB: null });
    expect(cmp.includesNonMeasurement).toBe(false);
    expect(compareRuns(runA, runFake, resultsA, resultsB).includesNonMeasurement).toBe(true);
    expect(compareRuns(runA, runA, resultsA, resultsA)).toMatchObject({ comparable: false });
    const other = normalizeRun(rawRun(ID_B, { ndcg: 0.7, split: "validation" }));
    expect(compareRuns(runA, other, resultsA, resultsB).reason).toContain("same questions");
    const answer = normalizeRun(rawRun(ID_B, { ndcg: 0.7, kind: "answer" }));
    expect(compareRuns(runA, answer, resultsA, resultsB).reason).toContain("different things");
  });
});

describe("components", () => {
  it("run list: caption, FAKE badge with text, best badge only on the measured winner, no best for fake", () => {
    const html = renderToStaticMarkup(<RunList runs={[runFake, runA, runB]} />);
    expect(html).toContain("<caption");
    expect(html).toContain("FAKE - NOT A MEASUREMENT");
    expect(html.match(/Best measured run/g)).toHaveLength(1);
    const bestRow = html.split("<tr").find((r) => r.includes("Best measured run"))!;
    expect(bestRow).toContain(ID_B);
    expect(html).toContain("golden-v1");
    expect(html).toContain("config abcdef0123");
    expect(html).toContain("commit c8612a3ff");
    expect(html).toContain("gemini-embedding-2");
    expect(html).toContain("[0.600, 0.800]"); // CI shown
    expect(html).not.toContain("/eval/compare?a=");
  });

  it("run list with a selected A offers compare links; empty list explains how to publish", () => {
    const html = renderToStaticMarkup(<RunList runs={[runA, runB]} compareA={ID_A} />);
    expect(html).toContain(`/eval/compare?a=${ID_A}&amp;b=${ID_B}`);
    expect(renderToStaticMarkup(<RunList runs={[]} />)).toContain("publish-run");
  });

  it("detail: stamp and warning for a fake run, per-category table with caption", () => {
    const html = renderToStaticMarkup(<><RunHeader run={runFake} /><CategoryTable run={runFake} /></>);
    expect(html).toContain("FAKE - NOT A MEASUREMENT");
    expect(html).toContain("not a measurement");
    expect(html).toContain("<caption");
    expect(html).toContain("single-fact");
    expect(renderToStaticMarkup(<RunHeader run={runA} />)).not.toContain("not a measurement");
  });

  it("drilldown lists failing questions with category text, candidates and gold match text", () => {
    const html = renderToStaticMarkup(<><FailureSummary results={resultsA} /><FailureDrilldown results={resultsA} kind="retrieval" /></>);
    expect(html).toContain("not retrieved");
    expect(html).toContain("ranked too low");
    expect(html).toContain("Question q1?");
    expect(html).not.toContain("Question q2?");
    expect(html).toContain("No match");
    expect(html).toContain("No trace id was recorded");
    const withTrace = normalizeResult(res("q9", "x", 0, "wrong version", { trace_id: "tr-1" }));
    expect(renderToStaticMarkup(<FailureDrilldown results={[withTrace]} kind="retrieval" />)).toContain("tr-1");
  });

  it("compare view shows deltas, the bootstrap note and the non-measurement warning", () => {
    const cmp = compareRuns(runA, runFake, resultsA, resultsB);
    const html = renderToStaticMarkup(<CompareResult a={runA} b={runFake} cmp={cmp} />);
    expect(html).toContain("not a measurement");
    expect(html).toContain("Paired percentile bootstrap");
    expect(html).toContain("Questions that changed");
    expect(html).toContain("<caption");
    const bad = compareRuns(runA, runA, resultsA, resultsA);
    expect(renderToStaticMarkup(<CompareResult a={runA} b={runA} cmp={bad} />)).toContain("two different runs");
  });
});

describe("access rule", () => {
  it("canViewEval keys the lookup by the user and requires an eval workspace; false for no row", async () => {
    m.sql.mockResolvedValueOnce([]);
    expect(await canViewEval("user_x")).toBe(false);
    const [q, values] = m.sql.mock.calls[0];
    expect(q).toContain(`"kind" = 'eval'`);
    expect(values).toEqual(["user_x", "user_x"]);
    m.sql.mockResolvedValueOnce([{ ok: 1 }]);
    expect(await canViewEval("owner")).toBe(true);
    expect(await canViewEval("")).toBe(false);
  });

  it("layout 404s for a signed-in non-eval user, a signed-out user and a lookup error; renders for an eval user", async () => {
    m.userId.mockResolvedValue("user_x");
    m.sql.mockResolvedValueOnce([]);
    await expect(EvalLayout({ children: null })).rejects.toThrow("NEXT_NOT_FOUND");
    m.userId.mockRejectedValue(new Error("Not authenticated"));
    await expect(EvalLayout({ children: null })).rejects.toThrow("NEXT_NOT_FOUND");
    m.userId.mockResolvedValue("user_x");
    m.sql.mockRejectedValueOnce(new Error("db down"));
    await expect(EvalLayout({ children: null })).rejects.toThrow("NEXT_NOT_FOUND");
    m.sql.mockResolvedValueOnce([{ ok: 1 }]);
    const el = await EvalLayout({ children: <p>inside</p> });
    expect(renderToStaticMarkup(el)).toContain("inside");
  });

  it("run detail page 404s on an unknown or non-uuid run and only queries eval-kind runs", async () => {
    m.sql.mockResolvedValue([]);
    await expect(EvalRunPage({ params: Promise.resolve({ runId: "not-a-uuid" }) })).rejects.toThrow("NEXT_NOT_FOUND");
    expect(m.sql).not.toHaveBeenCalled();
    await expect(EvalRunPage({ params: Promise.resolve({ runId: ID_A }) })).rejects.toThrow("NEXT_NOT_FOUND");
    expect(m.sql.mock.calls[0][0]).toContain(`'workspace_kind' = 'eval'`);
  });
});

describe("queries", () => {
  it("every read is restricted to meta.workspace_kind = eval and uuid-validated", async () => {
    m.sql.mockResolvedValue([]);
    await listEvalRuns();
    await getEvalRun(ID_A);
    await listEvalResults(ID_A);
    for (const [q] of m.sql.mock.calls) expect(q).toContain(`'workspace_kind' = 'eval'`);
    m.sql.mockClear();
    expect(await getEvalRun("x")).toBeNull();
    expect(await listEvalResults("x")).toEqual([]);
    expect(m.sql).not.toHaveBeenCalled();
    m.sql.mockResolvedValueOnce([]);
    await listEvalRuns(100000);
    expect(m.sql.mock.calls[0][1]).toEqual([200]);
  });
});

export type { EvalRun, EvalCaseResult };
