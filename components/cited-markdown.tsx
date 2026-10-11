"use client";

import ReactMarkdown from "react-markdown";
import { CitationLink } from "@/components/evidence-viewer";
import {
  linkCitations,
  parseCitationHref,
  type EvidenceByLabel,
} from "@/lib/evidence/citations";

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
export function CitedMarkdown({ text, evidence }: { text: string; evidence: EvidenceByLabel }) {
  return (
    <ReactMarkdown
      components={{
        a: ({ href, children, ...props }) => {
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
      {linkCitations(text, evidence)}
    </ReactMarkdown>
  );
}
