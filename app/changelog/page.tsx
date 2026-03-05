export default function ChangelogPage() {
  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto max-w-4xl px-6 py-10">
        <h1 className="font-serif text-4xl">Changelog</h1>
        <div className="mt-6 space-y-4">
          <article className="rounded-sm border border-border bg-card p-4">
            <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">March 2026</p>
            <h2 className="mt-2 font-serif text-2xl">SaaS Launch Upgrade</h2>
            <ul className="mt-3 space-y-1 text-sm text-muted-foreground">
              <li>Introduced pricing, billing APIs, and usage tracking.</li>
              <li>Added asynchronous ingestion pipeline for larger files.</li>
              <li>Improved chat output format: Answer, Key Points, Sources.</li>
              <li>Upgraded three-pane layout constraints and reset control.</li>
              <li>Stabilized chat/file delete actions across resizes.</li>
            </ul>
          </article>
        </div>
      </div>
    </main>
  );
}
