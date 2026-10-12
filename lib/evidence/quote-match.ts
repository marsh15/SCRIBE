/** Quote location inside rendered text (T23). Whitespace-normalised, so line wraps and indentation never break a match. */

const WS = /[\s ​]/;
const FOLD: Record<string, string> = { "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-" };

/** Collapse whitespace runs to one space, fold typographic quotes/dashes, trim; keep a map to original offsets. */
function normalize(text: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = []; // map[i] = index in the original of normalized char i
  let pendingSpace = -1;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (WS.test(ch)) {
      if (norm && pendingSpace < 0) pendingSpace = i;
      continue;
    }
    if (pendingSpace >= 0) {
      norm += " ";
      map.push(pendingSpace);
      pendingSpace = -1;
    }
    norm += (FOLD[ch] ?? ch).toLowerCase();
    map.push(i);
  }
  return { norm, map };
}

export function normalizeForMatch(text: string): string {
  return normalize(text).norm;
}

export type TextRange = { start: number; end: number }; // [start, end) in the ORIGINAL text

/** First occurrence of `quote` in `text`, ignoring whitespace differences and letter case. Null if absent/blank. */
export function findQuoteRange(text: string, quote: string): TextRange | null {
  const q = normalize(quote).norm;
  if (!q) return null;
  const { norm, map } = normalize(text);
  const at = norm.indexOf(q);
  if (at < 0) return null;
  return { start: map[at], end: map[at + q.length - 1] + 1 };
}

/** Split text into [before, match, after] around the quote, or null when it is not found. */
export function splitAroundQuote(text: string, quote: string): [string, string, string] | null {
  const r = findQuoteRange(text, quote);
  return r ? [text.slice(0, r.start), text.slice(r.start, r.end), text.slice(r.end)] : null;
}

export type PdfTextItem = { str: string };

/**
 * Text-match fallback for PDFs without a bbox (OCR pages, missing geometry): the indices of the pdf.js text items
 * (one page, reading order) that overlap the quote. Empty when the quote is not on that page's text layer.
 */
export function findQuoteInTextItems(items: readonly PdfTextItem[], quote: string): number[] {
  let joined = "";
  const starts: number[] = [];
  items.forEach((it, i) => {
    starts.push(joined.length);
    joined += it.str + (i < items.length - 1 ? " " : "");
  });
  const r = findQuoteRange(joined, quote);
  if (!r) return [];
  const hit: number[] = [];
  items.forEach((it, i) => {
    const s = starts[i];
    const e = s + it.str.length;
    if (it.str.trim() && s < r.end && e > r.start) hit.push(i);
  });
  return hit;
}

/** Range of data row `rowNumber` (1-based, header excluded, blank records skipped) in CSV text; quotes may span lines. */
export function findCsvRowRange(text: string, rowNumber: number): TextRange | null {
  if (!Number.isInteger(rowNumber) || rowNumber < 1) return null;
  let i = 0;
  let seen = 0; // non-blank records seen so far; the first is the header
  const n = text.length;
  if (text.charCodeAt(0) === 0xfeff) i = 1;
  while (i < n) {
    const start = i;
    let inQuotes = false;
    let blank = true;
    while (i < n) {
      const c = text[i];
      if (c === '"') inQuotes = !inQuotes;
      else if (!inQuotes && (c === "\n" || c === "\r")) break;
      if (!WS.test(c)) blank = false;
      i++;
    }
    const end = i;
    if (text[i] === "\r" && text[i + 1] === "\n") i += 2;
    else i++;
    if (blank) continue;
    seen++;
    if (seen - 1 === rowNumber) return { start, end };
  }
  return null;
}
