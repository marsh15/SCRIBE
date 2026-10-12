/**
 * Normalise the shape of a finished assistant answer. It never adds a Sources section: citations are the `[E#]`
 * labels the model wrote, each backed by a stored evidence item (T25/D14). The former "No source links were
 * generated" fallback fabricated a Sources section and is gone.
 */
export function ensureStructuredAnswer(text: string) {
  const base = text?.trim() ?? "";
  if (!base) return base;

  const hasAnswerHeader = /(^|\n)#{0,3}\s*Answer\b/i.test(base);
  const hasKeyPointsHeader = /(^|\n)#{0,3}\s*Key Points\b/i.test(base);

  let updated = base;

  if (!hasAnswerHeader) {
    updated = `## Answer\n${updated}`;
  }

  if (!hasKeyPointsHeader) {
    updated = `${updated}\n\n## Key Points\n- Summarized above.`;
  }

  return updated;
}
