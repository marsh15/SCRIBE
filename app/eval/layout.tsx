import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getUserId } from "@/lib/auth";
import { canViewEval } from "@/lib/eval/queries";

/**
 * Access gate for every /eval page: anyone who is not the owner or a member of the eval workspace (or who is signed out)
 * gets the same 404 as a path that does not exist.
 */
export default async function EvalLayout({ children }: { children: React.ReactNode }) {
  let allowed = false;
  try {
    allowed = await canViewEval(await getUserId());
  } catch {
    allowed = false;
  }
  if (!allowed) notFound();
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
          <Link
            href="/chat"
            className="inline-flex min-h-11 items-center gap-2 rounded-sm text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
            Back to chat
          </Link>
          <nav aria-label="Eval" className="flex gap-4 text-sm">
            <Link href="/eval" className="inline-flex min-h-11 items-center underline-offset-2 hover:underline">Runs</Link>
            <Link href="/eval/compare" className="inline-flex min-h-11 items-center underline-offset-2 hover:underline">Compare</Link>
          </nav>
        </div>
      </div>
      <div className="mx-auto w-full max-w-7xl space-y-6 px-4 py-6">{children}</div>
    </main>
  );
}
