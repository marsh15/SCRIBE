"use client";

import ReactMarkdown from "react-markdown";
import { AnswerEvidenceInspector } from "@/components/answer-evidence-inspector";
import { ClaimBadge, VerificationStatus } from "@/components/claim-badge";
import { CitationLink } from "@/components/evidence-viewer";
import {
  linkCitations,
  parseCitationHref,
  type EvidenceByLabel,
} from "@/lib/evidence/citations";
import { splitClaims } from "@/lib/evidence/claims";
import {
  annotateClaims,
  matchClaims,
  parseClaimHref,
  verificationPhase,
  type VerificationData,
} from "@/lib/evidence/verification";

/** Marker for a label that no stored evidence item backs. Not a link, not clickable. */
export function InvalidCitation({ label }: { label: string }) {
  return (
    <span
      data-testid="invalid-citation"
      title="Invalid citation: this label does not match any evidence retrieved for this answer"
      className="rounded-sm border border-dashed px-1 text-xs text-muted-foreground line-through"
    >
      [{label}]
      <span className="sr-only"> invalid citation, no matching evidence</span>
    </span>
  );
}

/** Assistant markdown with `[E#]` rendered as CitationLink (T23 viewer) when backed by evidence, else as an invalid mark. */
export function CitedMarkdown({
  text,
  evidence,
  verification,
  streaming = false,
  inspectable,
}: {
  text: string;
  evidence: EvidenceByLabel;
  /** Python-engine messages only (T26). Absent: no badges and no verification line. */
  verification?: VerificationData;
  streaming?: boolean;
  /** Explicitly include or exclude the inspector; by default it appears when answer data exists. */
  inspectable?: boolean;
}) {
  const claims = verification?.state === "done" ? splitClaims(text) : [];
  const matched = verification?.state === "done" ? matchClaims(claims, verification.claims) : new Map();
  const phase = verificationPhase(streaming, verification);
  const source = matched.size > 0 ? annotateClaims(text, claims, matched) : text;
  return (
    <>
    <ReactMarkdown
      components={{
        a: ({ href, children, ...props }) => {
          const claimIndex = parseClaimHref(href);
          if (claimIndex !== null) {
            const v = matched.get(claimIndex);
            return v ? <ClaimBadge status={v.status} /> : null;
          }
          const cite = parseCitationHref(href);
          if (cite?.kind === "valid") {
            const item = evidence.get(cite.label);
            if (item) {
              return (
                <CitationLink
                  itemId={item.itemId}
                  label={item.label}
                  sourceName={item.sourceName}
                  page={item.page}
                />
              );
            }
          }
          if (cite) return <InvalidCitation label={cite.label} />;
          const isInternal = href?.startsWith("/");
          return (
            <a
              href={href}
              target={isInternal ? undefined : "_blank"}
              rel={isInternal ? undefined : "noopener noreferrer"}
              className="text-rag hover:underline font-medium inline-flex items-center gap-1"
              {...props}
            >
              {children}
            </a>
          );
        },
      }}
    >
      {linkCitations(source, evidence)}
    </ReactMarkdown>
    {verification || phase === "generating" ? <VerificationStatus phase={phase} data={verification} /> : null}
    {(inspectable ?? (!!evidence.size || !!verification || streaming)) && (
      <AnswerEvidenceInspector evidence={evidence} verification={verification} streaming={streaming} />
    )}
    </>
  );
}
