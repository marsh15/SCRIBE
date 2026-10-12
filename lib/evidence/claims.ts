/**
 * Claim splitting for post-stream verification (T26, D14/D15).
 *
 * A claim is a Markdown sentence or bullet of the answer that asserts something. Headings, code, table separators,
 * questions, filler ("Summarized above.") and refusal / insufficient-evidence / unavailable messages are NOT claims.
 * A sentence that carries an `[E#]` label is always a claim. A factual sentence without one is still a claim: the
 * service marks it UNCITED. Offsets point into the original text so badges can be placed after each claim.
 */

export type Claim = {
  index: number;
  /** The sentence as written, `[E#]` labels included. */
  text: string;
  /** Offset just after the claim in the source text (badge insertion point). */
  end: number;
  labels: string[];
};

const LABEL = /\[(E\d{1,6})\]/g;
const ABBREVIATIONS = new Set(["e.g", "i.e", "etc", "vs", "dr", "mr", "ms", "mrs", "inc", "ltd", "no", "approx", "fig", "st"]);

const NON_CLAIM_PATTERNS: RegExp[] = [
  /\binsufficient evidence\b/i,
  /\bnot enough (information|evidence)\b/i,
  /\b(documents?|sources?|files?|knowledge base|evidence)\b[^.]*\b(do(es)? not|don't|doesn't|did not|couldn't|could not|cannot|can't)\b[^.]*\b(contain|cover|include|provide|mention|address|answer|specify)\b/i,
  /\b(couldn't|could not|unable to|cannot|can't) find\b[^.]*\b(information|answer|anything|relevant)/i,
  /\bupload (some |more |your )?(documents?|files?|sources?)\b/i,
  /^\W*(no sources|search unavailable|verification unavailable)\b/i,
  /^\W*summarized above\b/i,
  /^\W*please (retry|try again)\b/i,
];

export function extractLabels(text: string): string[] {
  const out: string[] = [];
  for (const m of text.matchAll(LABEL)) if (!out.includes(m[1])) out.push(m[1]);
  return out;
}

function plain(text: string): string {
  return text.replace(LABEL, " ").replace(/[*_`~>#|]/g, " ").replace(/\s+/g, " ").trim();
}

export function isClaimText(sentence: string): boolean {
  const labels = extractLabels(sentence);
  const p = plain(sentence);
  if (!/[\p{L}\p{N}]/u.test(p)) return false;
  if (labels.length > 0) return true;
  if (/\?\W*$/.test(p)) return false;
  if (NON_CLAIM_PATTERNS.some((re) => re.test(p))) return false;
  return p.split(" ").length >= 3;
}

/** Split one line segment [from, to) of `text` into sentences, keeping trailing `[E#]` with their sentence. */
function sentences(text: string, from: number, to: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  const seg = text.slice(from, to);
  const re = /[.!?]+["')\]]*((?:\s?\[E\d{1,6}\])*)/g;
  let start = 0;
  for (const m of seg.matchAll(re)) {
    const end = m.index! + m[0].length;
    const rest = seg.slice(end);
    if (rest.trim() !== "" && !/^\s+/.test(rest)) continue; // 3.5, a.b
    if (rest.trim() !== "" && /^\s+[a-z]/.test(rest)) continue; // lower-case continuation
    const word = seg.slice(start, m.index!).split(/\s+/).pop()?.toLowerCase().replace(/^\W+/, "") ?? "";
    if (m[0].startsWith(".") && (ABBREVIATIONS.has(word) || /^[a-z]$/.test(word))) continue;
    out.push([from + start, from + end]);
    start = end;
  }
  if (seg.slice(start).trim() !== "") out.push([from + start, to]);
  return out;
}

export function splitClaims(text: string): Claim[] {
  const claims: Claim[] = [];
  let inFence = false;
  let pos = 0;
  for (const line of text.split("\n")) {
    const lineStart = pos;
    pos += line.length + 1;
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence || line.trim() === "") continue;
    if (/^\s{0,3}#{1,6}(\s|$)/.test(line)) continue;
    if (/^[\s|:\-*_=]+$/.test(line)) continue; // table separators, rules
    const marker = /^\s*(?:>\s*)*(?:(?:[-*+]|\d+[.)])\s+)?/.exec(line)![0];
    const from = lineStart + marker.length;
    const to = lineStart + line.replace(/\s+$/, "").length;
    if (to <= from) continue;
    for (const [s, e] of sentences(text, from, to)) {
      const raw = text.slice(s, e);
      const sentence = raw.trim();
      if (!isClaimText(sentence)) continue;
      claims.push({ index: claims.length, text: sentence, end: s + raw.trimEnd().length, labels: extractLabels(sentence) });
    }
  }
  return claims;
}
