import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CategoryTable, FailureDrilldown, FailureSummary, RunHeader } from "@/components/eval/run-detail";
import { getEvalRun, listEvalResults } from "@/lib/eval/queries";

export const metadata: Metadata = { title: "Eval run" };
export const dynamic = "force-dynamic";

export default async function EvalRunPage({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  const run = await getEvalRun(runId);
  if (!run) notFound();
  const results = await listEvalResults(run.id);
  return (
    <>
      <RunHeader run={run} />
      <CategoryTable run={run} />
      <FailureSummary results={results} />
      <FailureDrilldown results={results} kind={run.kind} />
    </>
  );
}
