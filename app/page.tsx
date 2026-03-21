import Link from "next/link";
import { ArrowRight, BookOpen, Database, MessageSquare, Shield, Zap } from "lucide-react";
import { ThemeToggle } from "@/components/theme-toggle";
import { flags } from "@/lib/flags";
import { redirect } from "next/navigation";

export default function Home() {
  if (!flags.publicLandingEnabled) {
    redirect("/chat");
  }

  return (
    <main className="min-h-screen bg-background text-foreground relative overflow-hidden">
      {/* Subtle radial gradient for depth */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[600px] bg-[radial-gradient(ellipse_at_center,rgba(0,196,160,0.04)_0%,transparent_70%)]" />
      </div>

      <div className="relative mx-auto max-w-6xl px-6 py-10 sm:py-14">
        <header className="flex items-center justify-between border-b border-border pb-5">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-sm bg-primary text-primary-foreground flex items-center justify-center">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
                <path d="m15 5 4 4" />
              </svg>
            </div>
            <span className="font-serif text-2xl">Scribe</span>
          </div>
          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Link
              href="/pricing"
              className="rounded-sm border border-border px-3 py-2 text-xs font-mono uppercase tracking-wider hover:bg-muted transition-colors"
            >
              Pricing
            </Link>
            <Link
              href="/sign-in?redirect_url=/chat"
              className="rounded-sm bg-primary px-3 py-2 text-xs font-mono uppercase tracking-wider text-primary-foreground hover:opacity-90 transition-opacity"
            >
              Try Scribe
            </Link>
          </div>
        </header>

        <section className="pt-14 pb-10 animate-fade-up">
          <p className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground mb-5 animate-scale-in [animation-delay:100ms] opacity-0 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-[#00C4A0]"></span>
            AI Knowledge Workspace
          </p>
          <h1 className="font-serif text-4xl sm:text-6xl leading-tight max-w-4xl animate-fade-up [animation-delay:200ms] opacity-0">
            Upload documents, ask better questions, and get structured answers with source citations.
          </h1>
          <p className="mt-6 max-w-2xl text-base text-muted-foreground leading-relaxed animate-fade-up [animation-delay:300ms] opacity-0">
            Scribe indexes your files, runs semantic retrieval across your entire knowledge base,
            and returns customer-ready responses in clear sections.
          </p>

          <div className="mt-8 flex flex-wrap gap-3 animate-fade-up [animation-delay:400ms] opacity-0">
            <Link
              href="/sign-in?redirect_url=/chat"
              className="inline-flex items-center gap-2 rounded-sm bg-primary text-primary-foreground px-5 py-2.5 font-mono text-xs uppercase tracking-wider hover-lift transition-all"
            >
              Try It Now
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
            <Link
              href="/pricing"
              className="inline-flex items-center gap-2 rounded-sm border border-border px-5 py-2.5 font-mono text-xs uppercase tracking-wider hover:bg-muted hover-lift transition-all"
            >
              See Pricing
            </Link>
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 py-6">
          {[
            { icon: Database, title: "Document Ingestion", desc: "Upload PDF, TXT, MD, CSV, and DOCX (Recommended max length: ~25 pages).", delay: 500 },
            { icon: MessageSquare, title: "Structured Replies", desc: "Responses are formatted into Answer, Key Points, and Sources.", delay: 600 },
            { icon: Zap, title: "Three-Pane Workflow", desc: "Chat, navigation, and live RAG inspector with resizable constraints.", delay: 700 },
            { icon: Shield, title: "Per-User Isolation", desc: "Files, chats, usage, and billing are scoped to each authenticated user.", delay: 800 },
          ].map(({ icon: Icon, title, desc, delay }) => (
            <article
              key={title}
              className="rounded-sm border border-border bg-card p-5 animate-scale-in opacity-0 glow-hover hover-lift cursor-default group"
              style={{ animationDelay: `${delay}ms` }}
            >
              <div className="w-8 h-8 rounded-sm bg-[#00C4A0]/10 flex items-center justify-center mb-3 group-hover:bg-[#00C4A0]/20 transition-colors">
                <Icon className="h-4 w-4 text-[#00C4A0]" />
              </div>
              <h2 className="text-sm font-mono uppercase tracking-wider">{title}</h2>
              <p className="mt-2 text-sm text-muted-foreground leading-relaxed">{desc}</p>
            </article>
          ))}
        </section>

        <section className="mt-10 rounded-sm border border-border bg-gradient-to-br from-card via-card to-muted/50 p-6 sm:p-8 animate-fade-up opacity-0 [animation-delay:900ms]">
          <h3 className="font-serif text-3xl">How it works</h3>
          <ol className="mt-5 grid gap-4 sm:grid-cols-3 text-sm text-muted-foreground">
            {[
              { num: "01", text: "Upload files into your knowledge base." },
              { num: "02", text: "Ask questions in natural language." },
              { num: "03", text: "Get cited, customer-ready answers." },
            ].map(({ num, text }) => (
              <li key={num} className="rounded-sm bg-background/70 border border-border p-4 hover-lift transition-all">
                <span className="font-mono text-[#00C4A0] text-lg font-medium">{num}</span>
                <p className="mt-2">{text}</p>
              </li>
            ))}
          </ol>
        </section>

        <footer className="mt-16 border-t border-border pt-8 pb-6 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-muted-foreground">
              <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
            </svg>
            <span className="font-mono text-xs uppercase tracking-wider">Scribe &copy; {new Date().getFullYear()}</span>
          </div>
          <p className="text-sm font-mono text-muted-foreground">
            <a href="mailto:support@scribe-marsh.com" className="hover:text-foreground transition-colors">support@scribe-marsh.com</a>
          </p>
        </footer>
      </div>
    </main>
  );
}
