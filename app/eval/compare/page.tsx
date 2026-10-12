import type { Metadata } from "next";
import { CompareForm, CompareResult } from "@/components/eval/compare-view";
import { compareRuns, UUID_RE } from "@/lib/eval/model";
import { getEvalRun, listEvalResults, listEvalRuns } from "@/lib/eval/queries";

export const metadata: Metadata = { title: "Compare eval runs" };
export const dynamic = "force-dynamic";

export default async function EvalComparePage({ searchParams }: { searchParams: Promise<{ a?: string; b?: string }> }) {
  const { a, b } = await searchParams;
  const runs = await listEvalRuns();
  const [runA, runB] =
    a && b && UUID_RE.test(a) && UUID_RE.test(b) ? await Promise.all([getEvalRun(a), getEvalRun(b)]) : [null, null];
  let body: React.ReactNode = null;
  if (runA && runB) {
    const [ra, rb] = await Promise.all([listEvalResults(runA.id), listEvalResults(runB.id)]);
    body = <CompareResult a={runA} b={runB} cmp={compareRuns(runA, runB, ra, rb)} />;
  } else if (a || b) {
    body = <p role="alert" className="rounded-md border border-border p-3 text-sm">One of the selected runs was not found.</p>;
  }
  return (
    <>
      <header>
        <h1 className="text-xl font-semibold">Compare eval runs</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Pick two runs on the same dataset version and split. Differences are paired by question.
        </p>
      </header>
      <CompareForm runs={runs} a={runA?.id} b={runB?.id} />
      {body}
    </>
  );
}
