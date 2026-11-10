"use client";

import { useState } from "react";
import { CitationLink } from "@/components/evidence-viewer";
import type { EvidenceByLabel } from "@/lib/evidence/citations";
import { STATUS_META, type VerificationData } from "@/lib/evidence/verification";
import type { RagToolResult } from "@/lib/rag-types";
import { AnswerRetrievalDiagnostics } from "@/components/answer-retrieval-diagnostics";

/**
 * Things a reader might expect to see that the system does not store today. They are listed as such, never inferred from
 * a timestamp or a trace, so the inspector cannot claim more than the database holds.
 */
const NOT_RECORDED: ReadonlyArray<readonly [string, string]> = [
  ["Conflict explanation", "Disagreement between sources is stated in the answer text, not saved as structured data."],
  ["Verification time", "Claim checks are saved without how long they took."],
  ["Retrieval snapshot cutoff", "The search records when it ran, not a point-in-time cutoff of the index."],
];

/** Evidence and claim results belong to this answer, not the latest search in the chat. */
export function AnswerEvidenceInspector({
  evidence,
  verification,
  streaming,
  retrievalResults,
}: {
  evidence: EvidenceByLabel;
  verification?: VerificationData;
  streaming: boolean;
  retrievalResults: RagToolResult[];
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
          <AnswerRetrievalDiagnostics results={retrievalResults} />
          <section>
            <h3 className="font-mono text-[10px] uppercase text-muted-foreground">Claim verification</h3>
            {!verification && <p className="mt-1 text-muted-foreground">{streaming ? "Waiting for verification." : "Verification is unavailable for this answer."}</p>}
            {verification?.state === "verifying" && <p className="mt-1 text-muted-foreground">Verifying citations…</p>}
            {verification?.state === "unavailable" && <p className="mt-1 text-muted-foreground">Verification unavailable.</p>}
            {verification?.state === "done" && (verification.claims.length ? (
              <ul className="mt-1 space-y-2">
                {verification.claims.map((claim, index) => (
                  <li key={index} className="rounded-sm border border-border/50 p-2" data-testid="claim-row">
                    <p className="break-words">{claim.text}</p>
                    <p className="mt-1 flex flex-wrap items-center text-muted-foreground">
                      <span>{STATUS_META[claim.status].label}</span>
                      {claim.labels.length > 0 && <span aria-hidden="true">{" · "}</span>}
                      {claim.labels.map((label) => {
                        const cited = evidence.get(label);
                        return cited ? (
                          <CitationLink key={label} itemId={cited.itemId} label={cited.label} sourceName={cited.sourceName} page={cited.page} />
                        ) : (
                          <span key={label} className="px-1">[{label}] · no stored evidence</span>
                        );
                      })}
                    </p>
                    <p className="text-muted-foreground">{STATUS_META[claim.status].description}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-1 text-muted-foreground">No claims were available to verify.</p>)}
          </section>
          <section>
            <h3 className="font-mono text-[10px] uppercase text-muted-foreground">Not recorded</h3>
            <ul className="mt-1 space-y-1 text-muted-foreground">
              {NOT_RECORDED.map(([field, why]) => <li key={field}><strong>{field}:</strong> not recorded. {why}</li>)}
            </ul>
          </section>
        </div>
      )}
    </details>
  );
}
