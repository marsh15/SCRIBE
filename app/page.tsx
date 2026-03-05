import Link from "next/link";
import { ArrowRight, BookOpen, Database, MessageSquare, Shield, Zap } from "lucide-react";
import { flags } from "@/lib/flags";
import { redirect } from "next/navigation";

export default function Home() {
  if (!flags.publicLandingEnabled) {
    redirect("/chat");
  }

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-6xl px-6 py-10 sm:py-14">
        <header className="flex items-center justify-between border-b border-border pb-5">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-sm bg-primary text-primary-foreground flex items-center justify-center">
              <BookOpen className="h-4 w-4" />
            </div>
            <span className="font-serif text-2xl">Scribe</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/pricing"
              className="rounded-sm border border-border px-3 py-2 text-xs font-mono uppercase tracking-wider hover:bg-muted transition-colors"
            >
              Pricing
            </Link>
            <Link
              href="/sign-in?redirect_url=/chat"
              className="rounded-sm bg-primary px-3 py-2 text-xs font-mono uppercase tracking-wider text-primary-foreground hover:opacity-90"
            >
              Try Scribe
            </Link>
          </div>
        </header>

        <section className="pt-14 pb-10">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground mb-5">
            AI Knowledge Workspace
          </p>
          <h1 className="font-serif text-4xl sm:text-6xl leading-tight max-w-4xl">
            Upload documents, ask better questions, and get structured answers with source citations.
          </h1>
          <p className="mt-6 max-w-2xl text-base text-muted-foreground leading-relaxed">
            Scribe indexes your files, runs semantic retrieval across your entire knowledge base,
            and returns customer-ready responses in clear sections.
          </p>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link
              href="/sign-in?redirect_url=/chat"
              className="inline-flex items-center gap-2 rounded-sm bg-primary text-primary-foreground px-4 py-2.5 font-mono text-xs uppercase tracking-wider"
            >
              Try It Now
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
            <Link
              href="/pricing"
              className="inline-flex items-center gap-2 rounded-sm border border-border px-4 py-2.5 font-mono text-xs uppercase tracking-wider hover:bg-muted"
            >
              See Pricing
            </Link>
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 py-6">
          <article className="rounded-sm border border-border bg-card p-4">
            <Database className="h-4 w-4 text-[#00C4A0]" />
            <h2 className="mt-3 text-sm font-mono uppercase tracking-wider">Document Ingestion</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Upload PDF, TXT, MD, CSV, and DOCX up to 100 MB based on plan.
            </p>
          </article>
          <article className="rounded-sm border border-border bg-card p-4">
            <MessageSquare className="h-4 w-4 text-[#00C4A0]" />
            <h2 className="mt-3 text-sm font-mono uppercase tracking-wider">Structured Replies</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Responses are formatted into Answer, Key Points, and Sources.
            </p>
          </article>
          <article className="rounded-sm border border-border bg-card p-4">
            <Zap className="h-4 w-4 text-[#00C4A0]" />
            <h2 className="mt-3 text-sm font-mono uppercase tracking-wider">Three-Pane Workflow</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Chat, navigation, and live RAG inspector with resizable constraints.
            </p>
          </article>
          <article className="rounded-sm border border-border bg-card p-4">
            <Shield className="h-4 w-4 text-[#00C4A0]" />
            <h2 className="mt-3 text-sm font-mono uppercase tracking-wider">Per-User Isolation</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Files, chats, usage, and billing are scoped to each authenticated user.
            </p>
          </article>
        </section>

        <section className="mt-10 rounded-sm border border-border bg-gradient-to-r from-card to-muted p-6 sm:p-8">
          <h3 className="font-serif text-3xl">How it works</h3>
          <ol className="mt-4 grid gap-3 sm:grid-cols-3 text-sm text-muted-foreground">
            <li className="rounded-sm bg-background/70 border border-border p-3">
              1. Upload files into your knowledge base.
            </li>
            <li className="rounded-sm bg-background/70 border border-border p-3">
              2. Ask questions in natural language.
            </li>
            <li className="rounded-sm bg-background/70 border border-border p-3">
              3. Get cited, customer-ready answers.
            </li>
          </ol>
        </section>
      </div>
    </main>
  );
}
