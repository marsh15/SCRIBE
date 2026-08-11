import Link from "next/link";
import {
  ArrowRight,
  Check,
  ChevronRight,
  CircleDot,
  FileText,
  Github,
  LockKeyhole,
  MessageSquareText,
  Search,
  ServerCog,
  Sparkles,
} from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { flags } from "@/lib/flags";
import { redirect } from "next/navigation";

const pipeline = [
  { label: "Private upload", detail: "Vercel Blob", icon: LockKeyhole },
  { label: "Extract and chunk", detail: "Async worker", icon: ServerCog },
  { label: "Semantic retrieval", detail: "Postgres + pgvector", icon: Search },
  { label: "Grounded response", detail: "Gemini + citations", icon: Sparkles },
];

const decisions = [
  {
    title: "Tenant isolation at every boundary",
    body: "Files, chats, vector search, usage, and billing are scoped by the authenticated Clerk user, not filtered later in the UI.",
    tag: "Security",
  },
  {
    title: "Background ingestion that can recover",
    body: "Atomic job claims, bounded retries, exponential backoff, idempotent callbacks, and stale-job recovery keep document processing reliable.",
    tag: "Reliability",
  },
  {
    title: "Evidence users can inspect",
    body: "Answers link to retrieved passages. The RAG inspector exposes chunks, similarity, latency, and grounding signals without cluttering the default workflow.",
    tag: "Observability",
  },
];

function Logo() {
  return (
    <Link href="/" className="inline-flex min-h-11 items-center gap-2.5" aria-label="Scribe home">
      <span className="grid h-8 w-8 place-items-center rounded-sm bg-foreground text-background">
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
          <path d="m15 5 4 4" />
        </svg>
      </span>
      <span className="hidden font-serif text-2xl leading-none sm:inline">Scribe</span>
    </Link>
  );
}

export default function Home() {
  if (!flags.publicLandingEnabled) redirect("/chat");

  return (
    <main className="min-h-screen overflow-hidden bg-background text-foreground">
      <header className="border-b border-border/80">
        <div className="mx-auto flex min-h-16 max-w-7xl items-center justify-between gap-4 px-5 sm:px-8">
          <Logo />
          <nav aria-label="Primary navigation" className="flex items-center gap-1 sm:gap-2">
            <Link href="#engineering" className="hidden min-h-11 items-center px-3 text-sm text-muted-foreground transition-colors hover:text-foreground sm:inline-flex">
              Engineering
            </Link>
            <Link href="https://github.com/marsh15/SCRIBE" target="_blank" rel="noreferrer" className="hidden min-h-11 items-center gap-2 px-3 text-sm text-muted-foreground transition-colors hover:text-foreground sm:inline-flex">
              <Github className="h-4 w-4" aria-hidden="true" />
              <span>Source code</span>
            </Link>
            <ThemeToggle />
            <Link href="/sign-in?redirect_url=/chat" className="inline-flex min-h-11 items-center rounded-sm bg-primary px-4 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-85">
              <span className="sm:hidden">Open</span><span className="hidden sm:inline">Open workspace</span>
            </Link>
          </nav>
        </div>
      </header>

      <section className="relative border-b border-border/80">
        <div className="pointer-events-none absolute inset-0 landing-grid opacity-45" aria-hidden="true" />
        <div className="relative mx-auto grid max-w-7xl gap-12 px-5 py-16 sm:px-8 sm:py-24 lg:grid-cols-[0.9fr_1.1fr] lg:items-center lg:py-28">
          <div className="max-w-2xl">
            <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-border bg-background px-3 py-1.5 text-sm text-muted-foreground">
              <CircleDot className="h-3.5 w-3.5 text-rag" aria-hidden="true" />
              Evidence-first document intelligence
            </div>
            <h1 className="max-w-[12ch] font-serif text-5xl leading-[1.02] tracking-[-0.035em] text-balance sm:text-6xl lg:text-7xl">
              Ask your documents. Check every answer.
            </h1>
            <p className="mt-7 max-w-[62ch] text-lg leading-8 text-muted-foreground text-pretty">
              Scribe turns private PDFs, DOCX files, notes, and datasets into a searchable knowledge workspace. Every response is grounded in retrieved passages and linked back to its source.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link href="/sign-in?redirect_url=/chat" className="inline-flex min-h-12 items-center justify-center gap-2 rounded-sm bg-primary px-5 text-sm font-medium text-primary-foreground transition-opacity hover:opacity-85">
                Try the live workspace <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <Link href="https://github.com/marsh15/SCRIBE" target="_blank" rel="noreferrer" className="inline-flex min-h-12 items-center justify-center gap-2 rounded-sm border border-border bg-background px-5 text-sm font-medium transition-colors hover:bg-muted">
                Read the source <Github className="h-4 w-4" aria-hidden="true" />
              </Link>
            </div>
            <p className="mt-5 flex items-center gap-2 text-sm text-muted-foreground">
              <Check className="h-4 w-4 text-rag" aria-hidden="true" />
              Built end to end by Santosh Kumar with Next.js, TypeScript, PostgreSQL, and Gemini.
            </p>
          </div>

          <div className="relative mx-auto w-full max-w-2xl" aria-label="Scribe workspace preview">
            <div className="absolute -inset-8 -z-10 bg-rag/5 blur-3xl" aria-hidden="true" />
            <div className="overflow-hidden rounded-lg border border-border bg-card shadow-[0_8px_0_var(--border)]">
              <div className="flex h-11 items-center justify-between border-b border-border px-4">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-rag" />
                  workspace / policy-review
                </div>
                <span className="font-mono text-[11px] text-muted-foreground">3 sources ready</span>
              </div>
              <div className="grid min-h-[430px] grid-cols-[112px_1fr] sm:grid-cols-[160px_1fr_180px]">
                <aside className="border-r border-border p-3 sm:p-4" aria-label="Preview sources">
                  <p className="mb-4 text-xs font-medium">Sources</p>
                  {["Security policy", "Vendor contract", "Runbook.md"].map((file, index) => (
                    <div key={file} className={`mb-2 flex items-start gap-2 rounded-sm p-2 text-[11px] ${index === 0 ? "bg-muted text-foreground" : "text-muted-foreground"}`}>
                      <FileText className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
                      <span className="min-w-0 truncate">{file}</span>
                    </div>
                  ))}
                </aside>
                <div className="flex min-w-0 flex-col p-4 sm:p-5">
                  <div className="ml-auto max-w-[85%] rounded-md bg-primary px-3 py-2 text-xs leading-5 text-primary-foreground">
                    When must a security incident be reported?
                  </div>
                  <div className="mt-5 max-w-[95%] text-sm leading-6">
                    <div className="mb-2 flex items-center gap-2 font-medium"><MessageSquareText className="h-4 w-4 text-rag" aria-hidden="true" /> Answer</div>
                    Report a confirmed incident within 24 hours of discovery. Notify the security lead immediately when customer data may be affected.
                    <div className="mt-3 flex min-h-11 w-full items-center gap-2 rounded-sm border border-rag/40 bg-rag/5 px-3 text-left text-xs">
                      <span className="font-mono text-rag">[1]</span>
                      <span className="truncate">Security policy · page 8</span>
                      <ChevronRight className="ml-auto h-3.5 w-3.5" aria-hidden="true" />
                    </div>
                  </div>
                  <div className="mt-auto flex min-h-11 items-center rounded-sm border border-border px-3 text-xs text-muted-foreground">Ask about your sources…</div>
                </div>
                <aside className="hidden border-l border-border p-4 sm:block" aria-label="Preview evidence panel">
                  <p className="text-xs font-medium">Evidence</p>
                  <div className="mt-4 space-y-4 text-[11px] leading-5 text-muted-foreground">
                    <div><span className="mb-1 block text-foreground">Retrieval</span>10 chunks searched<br />3 passages selected</div>
                    <div><span className="mb-1 block text-foreground">Best match</span>Security policy<br /><span className="font-mono text-rag">0.86 similarity</span></div>
                    <div><span className="mb-1 block text-foreground">Grounding</span><span className="inline-flex items-center gap-1 text-foreground"><Check className="h-3 w-3 text-rag" /> Citation verified</span></div>
                  </div>
                </aside>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-border/80 bg-card" aria-labelledby="workflow-title">
        <div className="mx-auto max-w-7xl px-5 py-16 sm:px-8 sm:py-20">
          <div className="grid gap-8 lg:grid-cols-[0.7fr_1.3fr]">
            <div>
              <h2 id="workflow-title" className="font-serif text-4xl tracking-[-0.025em] text-balance">A complete path from file to verifiable answer.</h2>
              <p className="mt-4 max-w-lg leading-7 text-muted-foreground">The visible workflow stays simple while the system handles private storage, asynchronous processing, retrieval, streaming, and citations.</p>
            </div>
            <ol className="grid gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-2">
              {pipeline.map(({ label, detail, icon: Icon }, index) => (
                <li key={label} className="flex min-h-32 gap-4 bg-background p-5">
                  <span className="font-mono text-xs text-muted-foreground">0{index + 1}</span>
                  <div><Icon className="mb-4 h-5 w-5 text-rag" aria-hidden="true" /><h3 className="font-medium">{label}</h3><p className="mt-1 text-sm text-muted-foreground">{detail}</p></div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      <section id="engineering" className="mx-auto max-w-7xl px-5 py-16 sm:px-8 sm:py-24" aria-labelledby="engineering-title">
        <div className="max-w-3xl">
          <p className="mb-3 font-mono text-sm text-rag">Engineering decisions</p>
          <h2 id="engineering-title" className="font-serif text-4xl tracking-[-0.025em] text-balance sm:text-5xl">Designed for the failure modes a demo usually ignores.</h2>
          <p className="mt-5 max-w-2xl text-lg leading-8 text-muted-foreground">Scribe is a portfolio project with production-minded boundaries. The codebase includes migrations, route validation, webhook verification, ingestion policies, retrieval evaluation, and focused tests.</p>
        </div>
        <div className="mt-12 divide-y divide-border border-y border-border">
          {decisions.map((decision) => (
            <article key={decision.title} className="grid gap-3 py-7 md:grid-cols-[160px_0.8fr_1.2fr] md:items-start md:gap-8">
              <p className="font-mono text-xs text-rag">{decision.tag}</p>
              <h3 className="text-lg font-medium text-balance">{decision.title}</h3>
              <p className="max-w-2xl leading-7 text-muted-foreground">{decision.body}</p>
            </article>
          ))}
        </div>
        <div className="mt-10 flex flex-wrap gap-x-8 gap-y-3 text-sm text-muted-foreground">
          <span>Next.js 16 + React 19</span><span>Neon Postgres + pgvector</span><span>Clerk authentication</span><span>Vercel Blob</span><span>Vitest</span>
        </div>
      </section>

      <section className="border-y border-border bg-foreground text-background">
        <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-8 px-5 py-14 sm:px-8 md:flex-row md:items-center">
          <div><h2 className="font-serif text-4xl tracking-[-0.025em]">Bring a document. Ask the hard question.</h2><p className="mt-3 text-background/70">Inspect the answer, its evidence, and the retrieval path behind it.</p></div>
          <Link href="/sign-in?redirect_url=/chat" className="inline-flex min-h-12 shrink-0 items-center gap-2 rounded-sm bg-background px-5 text-sm font-medium text-foreground transition-opacity hover:opacity-85">Open Scribe <ArrowRight className="h-4 w-4" /></Link>
        </div>
      </section>

      <footer className="mx-auto flex max-w-7xl flex-col gap-5 px-5 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-8">
        <div><span className="text-foreground">Scribe</span> · Built by Santosh Kumar</div>
        <div className="flex flex-wrap gap-x-5"><Link href="/pricing" className="inline-flex min-h-11 items-center hover:text-foreground">Pricing</Link><Link href="/changelog" className="inline-flex min-h-11 items-center hover:text-foreground">Changelog</Link><Link href="https://github.com/marsh15/SCRIBE" target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center hover:text-foreground">GitHub</Link><a href="mailto:santoshkumarsp2004@gmail.com" className="inline-flex min-h-11 items-center hover:text-foreground">Contact</a></div>
      </footer>
    </main>
  );
}
