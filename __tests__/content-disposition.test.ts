import { describe, expect, it } from "vitest";
import { inlineDisposition } from "@/lib/files/content-disposition";

describe("inlineDisposition", () => {
  it("keeps a plain name readable", () => {
    expect(inlineDisposition("report.pdf")).toBe(`inline; filename="report.pdf"; filename*=UTF-8''report.pdf`);
  });

  it("never lets quotes, CR/LF or backslashes out of the header value", () => {
    const header = inlineDisposition('a"; filename=evil.exe\r\nSet-Cookie: x=1\\');
    expect(header).not.toMatch(/[\r\n]/);
    const quoted = header.match(/filename="([^"]*)"/);
    expect(quoted?.[1]).not.toMatch(/["\\\r\n]/);
    expect(header).toContain("filename*=UTF-8''a%22%3B%20filename%3Devil.exe%0D%0ASet-Cookie%3A%20x%3D1%5C");
  });

  it("encodes non-ASCII names for filename* and gives an ASCII fallback", () => {
    const header = inlineDisposition("Übersicht (Q3).pdf");
    expect(header).toContain(`filename*=UTF-8''%C3%9Cbersicht%20%28Q3%29.pdf`);
    expect(header).toMatch(/filename="_bersicht \(Q3\)\.pdf"/);
  });

  it("falls back to a fixed name for an empty one", () => {
    expect(inlineDisposition("")).toContain('filename="file"');
  });
});
