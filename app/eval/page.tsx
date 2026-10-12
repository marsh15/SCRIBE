import type { Metadata } from "next";
import { RunList } from "@/components/eval/run-list";
import { listEvalRuns } from "@/lib/eval/queries";
import { UUID_RE } from "@/lib/eval/model";

export const metadata: Metadata = { title: "Eval runs", description: "Measured retrieval and answer quality runs." };
export const dynamic = "force-dynamic";

export default async function EvalPage({ searchParams }: { searchParams: Promise<{ a?: string }> }) {
  const { a } = await searchParams;
  const runs = await listEvalRuns();
  const compareA = a && UUID_RE.test(a) && runs.some((r) => r.id === a) ? a : undefined;
  return (
    <>
      <header>
        <h1 className="text-xl font-semibold">Eval runs</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
          Runs against the eval workspace corpus. Every run records its dataset version, configuration hash, commit and model ids.
          A run that used a fake component or seeded test data is labelled and is never highlighted as best.
        </p>
      </header>
      <RunList runs={runs} compareA={compareA} />
    </>
  );
}
