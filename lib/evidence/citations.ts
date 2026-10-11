import { extractRagToolResult } from "@/lib/rag-types";

/** An evidence item as the answer can reference it: enough to open the viewer and name the link. */
export type CitationEvidence = {
  itemId: string;
  label: string;
  sourceName: string;
  page: number | null;
};

export type EvidenceByLabel = ReadonlyMap<string, CitationEvidence>;

const VALID_HREF = "#cite-";
const INVALID_HREF = "#invalid-cite-";

type PartLike = { type?: string; output?: unknown; result?: unknown };

/**
 * Evidence items a message can cite: only items stored by a real retrieval (T25) and carried in that message's own
 * search tool results (live `tool-*` parts or persisted `tool-invocation` parts). Labels are unique within a turn.
 */
export function collectEvidenceByLabel(parts: readonly unknown[] | undefined): Map<string, CitationEvidence> {
  const out = new Map<string, CitationEvidence>();
  for (const raw of parts ?? []) {
    const part = raw as PartLike;
    if (typeof part?.type !== "string" || !(part.type.startsWith("tool-") || part.type === "tool-invocation")) continue;
    const result = extractRagToolResult(part.output ?? part.result);
    for (const item of result?.evidence?.items ?? []) {
      if (!out.has(item.label)) {
        out.set(item.label, {
          itemId: item.id,
          label: item.label,
          sourceName: item.sourceName,
          page: item.pageStart,
        });
      }
    }
  }
  return out;
}

/**
 * Rewrite `[E3]` into a markdown link the renderer turns into a CitationLink. A label that is not in any bundle of
 * this message (for example `[E9]`) becomes an invalid-citation marker instead: never a link (full verification: T26).
 */
export function linkCitations(text: string, evidence: EvidenceByLabel): string {
  return text.replace(/\[(E\d+)\](?!\()/g, (_m, label: string) =>
    evidence.has(label) ? `[${label}](${VALID_HREF}${label})` : `[${label}](${INVALID_HREF}${label})`,
  );
}

export type CitationHref =
  | { kind: "valid"; label: string }
  | { kind: "invalid"; label: string }
  | null;

export function parseCitationHref(href: string | undefined): CitationHref {
  if (!href) return null;
  if (href.startsWith(VALID_HREF)) return { kind: "valid", label: href.slice(VALID_HREF.length) };
  if (href.startsWith(INVALID_HREF)) return { kind: "invalid", label: href.slice(INVALID_HREF.length) };
  return null;
}
