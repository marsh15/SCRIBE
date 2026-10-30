"use client";

import { useState } from "react";
import { CitationLink } from "@/components/evidence-viewer";
import type { EvidenceByLabel } from "@/lib/evidence/citations";
import { STATUS_META, type VerificationData } from "@/lib/evidence/verification";

/** Evidence and claim results belong to this answer, not the latest search in the chat. */
export function AnswerEvidenceInspector({
  evidence,
  verification,
  streaming,
}: {
  evidence: EvidenceByLabel;
  verification?: VerificationData;
  streaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details className="mt-3 border-t border-border/50 pt-2" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="cursor-pointer font-mono text-[10px] uppercase tracking-widest text-muted-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        Evidence inspector
      </summary>
      {open && (
        <div className="mt-3 space-y-3 text-xs not-prose" data-testid="answer-evidence-inspector">
          <section>
            <h3 className="font-mono text-[10px] uppercase text-muted-foreground">Retrieved evidence</h3>
            {evidence.size ? (
              <ul className="mt-1 space-y-1">
                {[...evidence.values()].map((item) => (
                  <li key={item.label} className="flex flex-wrap items-center gap-x-2 rounded-sm border border-border/50 px-2">
                    <CitationLink itemId={item.itemId} label={item.label} sourceName={item.sourceName} page={item.page} />
                    <span className="min-w-0 break-words">{item.sourceName} · {item.page == null ? "Page unavailable" : `Page ${item.page}`}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-1 text-muted-foreground">{streaming ? "Evidence is not yet available." : "No evidence is available for this answer."}</p>}
          </section>
          <section>
            <h3 className="font-mono text-[10px] uppercase text-muted-foreground">Claim verification</h3>
            {!verification && <p className="mt-1 text-muted-foreground">{streaming ? "Waiting for verification." : "Verification is unavailable for this answer."}</p>}
            {verification?.state === "verifying" && <p className="mt-1 text-muted-foreground">Verifying citations…</p>}
            {verification?.state === "unavailable" && <p className="mt-1 text-muted-foreground">Verification unavailable.</p>}
            {verification?.state === "done" && (verification.claims.length ? (
              <ul className="mt-1 space-y-2">
                {verification.claims.map((claim, index) => (
                  <li key={index} className="rounded-sm border border-border/50 p-2">
                    <p className="break-words">{claim.text}</p>
                    <p className="mt-1 text-muted-foreground">{STATUS_META[claim.status].label}{claim.labels.length > 0 ? ` · ${claim.labels.map((label) => `[${label}]`).join(" ")}` : ""}</p>
                    <p className="text-muted-foreground">{STATUS_META[claim.status].description}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-1 text-muted-foreground">No claims were available to verify.</p>)}
          </section>
        </div>
      )}
    </details>
  );
}
