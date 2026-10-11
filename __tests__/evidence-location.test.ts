import { describe, expect, it } from "vitest";
import { bboxToViewportRect, normalizeRotation, unionBbox } from "@/lib/evidence/geometry";
import {
  findCsvRowRange,
  findQuoteInTextItems,
  findQuoteRange,
  splitAroundQuote,
} from "@/lib/evidence/quote-match";
import { citationAccessibleName, detectFormat, pageLabel, planOpen } from "@/lib/evidence/open-plan";
import type { EvidenceAvailable } from "@/lib/evidence/types";

describe("bboxToViewportRect", () => {
  const g = { pageWidth: 600, pageHeight: 800, scale: 2 };
  it("scales top-left-origin points to pixels", () => {
    expect(bboxToViewportRect([10, 20, 110, 40], g)).toEqual({ left: 20, top: 40, width: 200, height: 40 });
  });
  it("rotates 90/180/270 clockwise within the page frame", () => {
    const b = [10, 20, 110, 40] as const;
    expect(bboxToViewportRect(b, { ...g, scale: 1, rotation: 90 })).toEqual({ left: 760, top: 10, width: 20, height: 100 });
    expect(bboxToViewportRect(b, { ...g, scale: 1, rotation: 180 })).toEqual({ left: 490, top: 760, width: 100, height: 20 });
    expect(bboxToViewportRect(b, { ...g, scale: 1, rotation: 270 })).toEqual({ left: 20, top: 490, width: 20, height: 100 });
    expect(bboxToViewportRect(b, { ...g, scale: 1, rotation: -90 })).toEqual(bboxToViewportRect(b, { ...g, scale: 1, rotation: 270 }));
    expect(normalizeRotation(450)).toBe(90);
  });
  it("refuses degenerate input instead of guessing", () => {
    expect(bboxToViewportRect([10, 20, 5, 40], g)).toBeNull();
    expect(bboxToViewportRect([10, 20, 110, 40], { ...g, scale: 0 })).toBeNull();
    expect(bboxToViewportRect([10, 20, 110, NaN], g)).toBeNull();
    expect(bboxToViewportRect([10, 20, 110, 40], { ...g, pageWidth: 0 })).toBeNull();
  });
  it("unions only valid boxes", () => {
    expect(unionBbox([[0, 0, 10, 10], null, [5, 5, 20, 30], [3, 3, 1, 1]])).toEqual([0, 0, 20, 30]);
    expect(unionBbox([null])).toBeNull();
  });
});

describe("quote matching", () => {
  it("matches across wrapped lines, indentation and typographic quotes, and maps back to original offsets", () => {
    const text = "Intro.\n  Employees   get\n\t20 days of “paid” leave.\nMore.";
    const r = findQuoteRange(text, 'employees get 20 days of "paid" leave');
    expect(r).not.toBeNull();
    expect(text.slice(r!.start, r!.end)).toBe("Employees   get\n\t20 days of “paid” leave");
  });
  it("splits markdown/plain text around the quote and returns null when absent", () => {
    expect(splitAroundQuote("# H\n\nThe quick  brown fox.", "quick brown")).toEqual(["# H\n\nThe ", "quick  brown", " fox."]);
    expect(splitAroundQuote("nothing here", "quick brown")).toBeNull();
    expect(findQuoteRange("abc", "   ")).toBeNull();
  });
  it("finds the quote in pdf.js text items (text-match fallback)", () => {
    const items = [{ str: "Warsaw on-time" }, { str: "delivery was" }, { str: "" }, { str: "97.2% in Q3" }, { str: "Other" }];
    expect(findQuoteInTextItems(items, "on-time delivery was 97.2%")).toEqual([0, 1, 3]);
    expect(findQuoteInTextItems(items, "missing text")).toEqual([]);
  });
  it("finds a CSV data row (header excluded, blank records skipped, quoted newlines kept)", () => {
    const csv = '﻿name,note\n\nAcme,"line one\nline two"\r\nBeta,ok\n';
    const r1 = findCsvRowRange(csv, 1)!;
    expect(csv.slice(r1.start, r1.end)).toBe('Acme,"line one\nline two"');
    const r2 = findCsvRowRange(csv, 2)!;
    expect(csv.slice(r2.start, r2.end)).toBe("Beta,ok");
    expect(findCsvRowRange(csv, 3)).toBeNull();
    expect(findCsvRowRange(csv, 0)).toBeNull();
  });
});

const base: EvidenceAvailable = {
  sourceDeleted: false, itemId: "i", label: "E1", sourceId: 1, sourceName: "Handbook.pdf", sourceVersionId: "v",
  versionNumber: 1, mimeType: "application/pdf", quote: "q", headingPath: [], page: 3, ocr: false, ocrNotice: null,
  spans: [{ blockIndex: 0, page: 3, bbox: [1, 2, 3, 4], ocr: false, rowNumber: null, charStart: 0, charEnd: 1 }],
};

describe("planOpen", () => {
  it("draws the bbox for a text-layer PDF page", () => {
    const p = planOpen(base);
    expect(p.highlight).toEqual({ kind: "bbox", boxes: [[1, 2, 3, 4]] });
    expect(p.pageLabel).toBe("Page 3");
    expect(p.ocrNotice).toBeNull();
  });
  it("never draws a bbox for OCR spans, falls back to text match and shows the notice", () => {
    const p = planOpen({ ...base, ocr: true, spans: [{ ...base.spans[0], ocr: true }] });
    expect(p.highlight).toEqual({ kind: "text" });
    expect(p.ocrNotice).toBe("OCR text — verify against original");
    // even if one span claims no OCR but the item is OCR, or a bbox slipped through
    expect(planOpen({ ...base, ocr: true }).highlight).toEqual({ kind: "text" });
    expect(planOpen({ ...base, spans: [{ ...base.spans[0], ocr: true }] }).highlight).toEqual({ kind: "text" });
  });
  it("falls back to text match when there is no bbox, ignoring boxes from other pages", () => {
    expect(planOpen({ ...base, spans: [{ ...base.spans[0], bbox: null }] }).highlight).toEqual({ kind: "text" });
    expect(planOpen({ ...base, spans: [{ ...base.spans[0], page: 9 }] }).highlight).toEqual({ kind: "text" });
  });
  it("says 'page unavailable' and never estimates a page (INV-7)", () => {
    const p = planOpen({ ...base, page: null });
    expect(p.pageLabel).toBe("page unavailable");
    expect(p.page).toBeNull();
    expect(p.highlight).toEqual({ kind: "text" }); // no page: no box either
    expect(pageLabel(0)).toBe("page unavailable");
    expect(pageLabel(null)).toBe("page unavailable");
    expect(pageLabel(2.5)).toBe("page unavailable");
    expect(p.accessibleName).toBe("Citation E1: Handbook.pdf, page unavailable");
  });
  it("picks the highlight per format", () => {
    const csv = planOpen({
      ...base, sourceName: "prices.csv", mimeType: "text/csv", page: null,
      spans: [{ ...base.spans[0], page: null, bbox: null, rowNumber: 12 }],
    });
    expect(csv.highlight).toEqual({ kind: "csv-row", rowNumber: 12 });
    for (const [name, mime, fmt] of [["a.md", "text/markdown", "md"], ["a.txt", "text/plain", "txt"],
      ["a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"]] as const) {
      const p = planOpen({ ...base, sourceName: name, mimeType: mime, page: null, spans: [] });
      expect(p.format).toBe(fmt);
      expect(p.highlight).toEqual({ kind: "text" });
    }
    expect(detectFormat(null, "x.PDF")).toBe("pdf");
    expect(detectFormat(null, "x.bin")).toBe("other");
  });
  it("names the citation for screen readers with source and page", () => {
    expect(citationAccessibleName("Handbook.pdf", 3)).toBe("Open citation: Handbook.pdf, page 3");
  });
});
