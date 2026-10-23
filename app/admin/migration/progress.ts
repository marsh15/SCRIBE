export function progressPercent(embedded: number, expected: number): number | null {
  if (expected <= 0) return null;
  return Math.min(100, Math.round((1000 * embedded) / expected) / 10);
}
