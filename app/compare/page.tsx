import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { CompareLab } from "@/components/compare-lab";

export const metadata: Metadata = {
  title: "Retrieval lab",
  description: "Compare retrieval profiles side by side on one database snapshot.",
};

export default function ComparePage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="border-b border-border">
        <div className="mx-auto flex h-14 max-w-7xl items-center px-4">
          <Link
            href="/chat"
            className="inline-flex min-h-11 items-center gap-2 rounded-sm text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
            Back to chat
          </Link>
        </div>
      </div>
      <CompareLab />
    </main>
  );
}
