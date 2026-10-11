export const OCR_NOTICE = "OCR text — verify against original";
export const SOURCE_DELETED_NOTICE = "Source deleted";
export const PAGE_UNAVAILABLE = "page unavailable";

/** A source span as the Python service reports it. bbox is PDF points, origin top-left (D6). Null for OCR text. */
export type EvidenceSpan = {
  blockIndex: number;
  page: number | null;
  bbox: [number, number, number, number] | null;
  ocr: boolean;
  rowNumber: number | null;
  charStart: number | null;
  charEnd: number | null;
};

export type EvidenceAvailable = {
  sourceDeleted: false;
  itemId: string;
  label: string;
  sourceId: number;
  sourceName: string;
  sourceVersionId: string | null;
  versionNumber: number | null;
  mimeType: string | null;
  quote: string;
  headingPath: string[];
  page: number | null;
  ocr: boolean;
  ocrNotice: string | null;
  spans: EvidenceSpan[];
};

export type EvidenceDeleted = {
  sourceDeleted: true;
  itemId: string;
  label: string;
  notice: typeof SOURCE_DELETED_NOTICE;
};

export type EvidenceOpen = EvidenceAvailable | EvidenceDeleted;
