/**
 * `Content-Disposition: inline` for a user-controlled file name. The name never appears raw in the header: quotes,
 * backslashes, CR/LF and non-ASCII could otherwise break out of the quoted string (header injection). An ASCII fallback
 * goes in `filename`, the exact name in RFC 5987/8187 `filename*`.
 */
export function inlineDisposition(name: string): string {
  const ascii = name.replace(/[^\x20-\x7e]|["\\%]/g, "_").slice(0, 200) || "file";
  const encoded = encodeURIComponent(name).replace(/['()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
  return `inline; filename="${ascii}"; filename*=UTF-8''${encoded}`;
}
