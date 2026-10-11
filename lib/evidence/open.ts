import { callIntelligence } from "@/lib/intelligence-client";
import {
  SOURCE_DELETED_NOTICE,
  type EvidenceOpen,
  type EvidenceSpan,
} from "@/lib/evidence/types";

type PySpan = {
  block_index: number;
  page: number | null;
  bbox: number[] | null;
  ocr: boolean;
  row_number: number | null;
  char_start: number | null;
  char_end: number | null;
};

type PyOpen =
  | { source_deleted: true; item_id: string; label: string; notice?: string }
  | {
      source_deleted: false;
      item_id: string;
      label: string;
      source_id: number;
      source_name: string;
      source_version_id: string | null;
      version_number: number | null;
      mime_type: string | null;
      quote: string;
      heading_path: string[];
      page: number | null;
      ocr: boolean;
      ocr_notice: string | null;
      spans: PySpan[];
    };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isUuid = (v: string) => UUID.test(v);

export type OpenResult =
  | { ok: true; evidence: EvidenceOpen }
  | { ok: false; status: 404 | 502 };

function toSpan(s: PySpan, ocr: boolean): EvidenceSpan {
  const bbox = !ocr && !s.ocr && s.bbox?.length === 4 ? (s.bbox as EvidenceSpan["bbox"]) : null;
  return {
    blockIndex: s.block_index,
    page: s.page,
    bbox,
    ocr: s.ocr,
    rowNumber: s.row_number,
    charStart: s.char_start,
    charEnd: s.char_end,
  };
}

/** Ask the Python service to open one evidence item. The workspace comes from the caller's session only. */
export async function openEvidence(itemId: string, user: { userId: string; workspaceId: string }): Promise<OpenResult> {
  let res: Response;
  try {
    res = await callIntelligence(
      "/evidence/open",
      { sub: user.userId, wid: user.workspaceId, scope: "evidence_read" },
      { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ item_id: itemId }) },
    );
  } catch {
    return { ok: false, status: 502 };
  }
  if (res.status === 404) return { ok: false, status: 404 };
  if (!res.ok) return { ok: false, status: 502 };
  const j = (await res.json()) as PyOpen;
  if (j.source_deleted) {
    // The quote, page and name are withheld: only the label and the notice survive.
    return { ok: true, evidence: { sourceDeleted: true, itemId: j.item_id, label: j.label, notice: SOURCE_DELETED_NOTICE } };
  }
  return {
    ok: true,
    evidence: {
      sourceDeleted: false,
      itemId: j.item_id,
      label: j.label,
      sourceId: j.source_id,
      sourceName: j.source_name,
      sourceVersionId: j.source_version_id,
      versionNumber: j.version_number,
      mimeType: j.mime_type,
      quote: j.quote,
      headingPath: j.heading_path ?? [],
      page: j.page,
      ocr: j.ocr,
      ocrNotice: j.ocr_notice,
      spans: (j.spans ?? []).map((s) => toSpan(s, j.ocr)),
    },
  };
}
