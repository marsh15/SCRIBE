import { isValidBbox, type Bbox } from "@/lib/evidence/geometry";
import {
  OCR_NOTICE,
  PAGE_UNAVAILABLE,
  type EvidenceAvailable,
} from "@/lib/evidence/types";

export type EvidenceFormat = "pdf" | "docx" | "md" | "txt" | "csv" | "other";

export type Highlight =
  | { kind: "bbox"; boxes: Bbox[] }
  | { kind: "text" } // match the quote inside the rendered text
  | { kind: "csv-row"; rowNumber: number }
  | { kind: "none" };

export type OpenPlan = {
  format: EvidenceFormat;
  page: number | null;
  /** "Page 3" or "page unavailable": an estimated page is never shown (INV-7). */
  pageLabel: string;
  highlight: Highlight;
  ocrNotice: string | null;
  /** Screen-reader name for the citation link and the viewer. */
  accessibleName: string;
};

export function detectFormat(mimeType: string | null, name: string): EvidenceFormat {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  const mime = (mimeType ?? "").toLowerCase();
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (mime.includes("wordprocessingml") || ext === "docx") return "docx";
  if (mime === "text/csv" || ext === "csv") return "csv";
  if (mime === "text/markdown" || ext === "md" || ext === "markdown") return "md";
  if (mime === "text/plain" || ext === "txt") return "txt";
  return "other";
}

export function pageLabel(page: number | null | undefined): string {
  return typeof page === "number" && Number.isInteger(page) && page > 0 ? `Page ${page}` : PAGE_UNAVAILABLE;
}

export function citationAccessibleName(sourceName: string, page: number | null | undefined, label?: string): string {
  return `${label ? `Citation ${label}: ` : "Open citation: "}${sourceName}, ${pageLabel(page).toLowerCase()}`;
}

export function planOpen(ev: EvidenceAvailable): OpenPlan {
  const format = detectFormat(ev.mimeType, ev.sourceName);
  const page = typeof ev.page === "number" && ev.page > 0 ? ev.page : null;
  const ocr = ev.ocr || ev.spans.some((s) => s.ocr);
  let highlight: Highlight = { kind: "text" };

  if (format === "pdf") {
    // OCR text has no trustworthy geometry: never draw a box for it, match the quote instead.
    const boxes = ocr
      ? []
      : ev.spans
          .filter((s) => !s.ocr && s.page === page && isValidBbox(s.bbox))
          .map((s) => s.bbox as Bbox);
    highlight = page !== null && boxes.length ? { kind: "bbox", boxes } : { kind: "text" };
  } else if (format === "csv") {
    const row = ev.spans.find((s) => s.rowNumber !== null)?.rowNumber ?? null;
    highlight = row !== null ? { kind: "csv-row", rowNumber: row } : { kind: "text" };
  }

  return {
    format,
    page,
    pageLabel: pageLabel(page),
    highlight,
    ocrNotice: ocr ? (ev.ocrNotice ?? OCR_NOTICE) : null,
    accessibleName: citationAccessibleName(ev.sourceName, page, ev.label),
  };
}
