import { sql } from "@/lib/db-config";
import {
  isValidSourceId,
  toPage,
  toWindow,
  type Page,
  type PageRequest,
  type SpanView,
  type StructureBlock,
} from "@/lib/files/source-detail-paging";

/**
 * Read side of the Source detail page (T24). Every query is scoped by the server-derived workspace id AND the caller's
 * user id AND `files.deleted_at IS NULL` inside the statement (INV-1/2), so a foreign or soft-deleted Source yields no
 * rows (callers turn that into a 404). Page sizes are clamped here (default 50, max 200). Document text only leaves
 * the database as bounded previews and is never logged (INV-13).
 *
 * The SQL in each function is also executed by intelligence/tests/integration/test_source_detail.py, which extracts the
 * first sql`...` after the function name: keep placeholders as plain `${name}` identifiers.
 */

type Row = Record<string, unknown>;

const iso = (value: unknown): string | null =>
  value === null || value === undefined ? null : value instanceof Date ? value.toISOString() : String(value);
const num = (value: unknown): number | null => (value === null || value === undefined ? null : Number(value));

export interface SourceHeader {
  id: number;
  name: string;
  type: string;
  size: number;
  status: string;
  processingError: string | null;
  createdAt: string;
  reuploadRequiredAt: string | null;
  reuploadReason: string | null;
  activeVersionId: string | null;
  activeVersionNumber: number | null;
}

export async function getSourceHeader(workspaceId: string, userId: string, sourceId: number): Promise<SourceHeader | null> {
  if (!isValidSourceId(sourceId)) return null;
  const rows = (await sql`
    SELECT f."id", f."name", f."type", f."size", f."status", f."processing_error", f."created_at",
           f."reupload_required_at", f."reupload_reason", f."active_version_id",
           av."version_number" AS active_version_number
    FROM "files" f
    LEFT JOIN "source_versions" av ON av."id" = f."active_version_id" AND av."workspace_id" = f."workspace_id"
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
  `) as Row[];
  const r = rows[0];
  if (!r) return null;
  return {
    id: Number(r.id),
    name: String(r.name),
    type: String(r.type),
    size: Number(r.size),
    status: String(r.status),
    processingError: (r.processing_error as string | null) ?? null,
    createdAt: iso(r.created_at) ?? "",
    reuploadRequiredAt: iso(r.reupload_required_at),
    reuploadReason: (r.reupload_reason as string | null) ?? null,
    activeVersionId: (r.active_version_id as string | null) ?? null,
    activeVersionNumber: num(r.active_version_number),
  };
}

/** Blocks of the Source's active version in document order. `null` = no such Source for this caller. */
export async function listStructure(
  workspaceId: string,
  userId: string,
  sourceId: number,
  request?: PageRequest
): Promise<Page<StructureBlock> | null> {
  const window = toWindow(request);
  if (!isValidSourceId(sourceId)) return null;
  const header = await getSourceHeader(workspaceId, userId, sourceId);
  if (!header) return null;
  const fetchLimit = window.limit + 1;
  const offset = window.offset;
  const rows = (await sql`
    SELECT b."id", b."block_index", b."type", b."heading_path", b."page", b."ocr", b."row_number",
           b."char_start", b."char_end", left(b."text", 240) AS preview, length(b."text") AS text_length
    FROM "files" f
    INNER JOIN "document_blocks" b
      ON b."source_version_id" = f."active_version_id" AND b."workspace_id" = f."workspace_id"
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
    ORDER BY b."block_index"
    LIMIT ${fetchLimit}::int OFFSET ${offset}::int
  `) as Row[];
  return toPage(
    rows.map((r) => ({
      id: String(r.id),
      blockIndex: Number(r.block_index),
      type: String(r.type),
      headingPath: Array.isArray(r.heading_path) ? (r.heading_path as string[]) : [],
      page: num(r.page),
      ocr: r.ocr === true,
      rowNumber: num(r.row_number),
      charStart: num(r.char_start),
      charEnd: num(r.char_end),
      preview: String(r.preview ?? ""),
      textLength: Number(r.text_length ?? 0),
    })),
    window
  );
}

export type ChunkKind = "all" | "parent" | "child";

export interface ChunkView {
  id: string;
  parentId: string | null;
  chunkIndex: number;
  kind: "parent" | "child";
  tokenCount: number | null;
  headingText: string | null;
  excerpt: string;
  contentLength: number;
  childCount: number | null;
  spans: SpanView[];
}

/** Parent and child chunks of the active, ACTIVE-status generation with their source spans. */
export async function listChunks(
  workspaceId: string,
  userId: string,
  sourceId: number,
  request?: PageRequest & { kind?: ChunkKind }
): Promise<Page<ChunkView> | null> {
  const window = toWindow(request);
  if (!isValidSourceId(sourceId)) return null;
  const header = await getSourceHeader(workspaceId, userId, sourceId);
  if (!header) return null;
  const kind: ChunkKind = request?.kind === "parent" || request?.kind === "child" ? request.kind : "all";
  const fetchLimit = window.limit + 1;
  const offset = window.offset;
  const rows = (await sql`
    SELECT c."id", c."parent_id", c."chunk_index", (c."parent_id" IS NULL) AS is_parent, c."token_count",
           c."heading_text", left(c."content", 400) AS excerpt, length(c."content") AS content_length,
           CASE WHEN c."parent_id" IS NULL
                THEN (SELECT count(*) FROM "chunks" k WHERE k."parent_id" = c."id" AND k."workspace_id" = c."workspace_id")
           END AS child_count,
           sp."spans"
    FROM "files" f
    INNER JOIN LATERAL (
      SELECT g."id" FROM "index_generations" g
      WHERE g."source_version_id" = f."active_version_id" AND g."workspace_id" = f."workspace_id" AND g."status" = 'ACTIVE'
      ORDER BY g."created_at" DESC LIMIT 1
    ) ag ON true
    INNER JOIN "chunks" c ON c."generation_id" = ag."id" AND c."workspace_id" = f."workspace_id"
    LEFT JOIN LATERAL (
      SELECT coalesce(json_agg(json_build_object('page', s."page", 'charStart', s."char_start", 'charEnd', s."char_end")
                               ORDER BY s."char_start" NULLS LAST, s."id"), '[]'::json) AS "spans"
      FROM (
        SELECT x."id", x."page", x."char_start", x."char_end" FROM "chunk_source_spans" x
        WHERE x."chunk_id" = c."id" AND x."workspace_id" = c."workspace_id"
        ORDER BY x."char_start" NULLS LAST, x."id" LIMIT 20
      ) s
    ) sp ON true
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
      AND (${kind}::text = 'all' OR ((${kind}::text = 'parent') = (c."parent_id" IS NULL)))
    ORDER BY c."chunk_index"
    LIMIT ${fetchLimit}::int OFFSET ${offset}::int
  `) as Row[];
  return toPage(
    rows.map((r) => ({
      id: String(r.id),
      parentId: (r.parent_id as string | null) ?? null,
      chunkIndex: Number(r.chunk_index),
      kind: r.is_parent === true ? ("parent" as const) : ("child" as const),
      tokenCount: num(r.token_count),
      headingText: (r.heading_text as string | null) ?? null,
      excerpt: String(r.excerpt ?? ""),
      contentLength: Number(r.content_length ?? 0),
      childCount: num(r.child_count),
      spans: Array.isArray(r.spans) ? (r.spans as Row[]).map((s) => ({ page: num(s.page), charStart: num(s.charStart), charEnd: num(s.charEnd) })) : [],
    })),
    window
  );
}

export interface JobHistoryEntry {
  id: number;
  versionId: string | null;
  versionNumber: number | null;
  status: string;
  stage: string | null;
  attempts: number;
  errorCode: string | null;
  lastError: string | null;
  nextRetryAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** Ingestion jobs of the Source across all its versions, newest first. */
export async function listIngestionHistory(
  workspaceId: string,
  userId: string,
  sourceId: number,
  request?: PageRequest
): Promise<Page<JobHistoryEntry> | null> {
  const window = toWindow(request);
  if (!isValidSourceId(sourceId)) return null;
  const header = await getSourceHeader(workspaceId, userId, sourceId);
  if (!header) return null;
  const fetchLimit = window.limit + 1;
  const offset = window.offset;
  const rows = (await sql`
    SELECT j."id", j."source_version_id", v."version_number", j."status", j."stage", j."attempts", j."error_code",
           j."last_error", j."next_retry_at", j."started_at", j."finished_at", j."created_at", j."updated_at"
    FROM "files" f
    INNER JOIN "ingestion_jobs" j ON j."file_id" = f."id"
    LEFT JOIN "source_versions" v ON v."id" = j."source_version_id" AND v."workspace_id" = f."workspace_id"
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
    ORDER BY j."created_at" DESC, j."id" DESC
    LIMIT ${fetchLimit}::int OFFSET ${offset}::int
  `) as Row[];
  return toPage(
    rows.map((r) => ({
      id: Number(r.id),
      versionId: (r.source_version_id as string | null) ?? null,
      versionNumber: num(r.version_number),
      status: String(r.status),
      stage: (r.stage as string | null) ?? null,
      attempts: Number(r.attempts ?? 0),
      errorCode: (r.error_code as string | null) ?? null,
      lastError: (r.last_error as string | null) ?? null,
      nextRetryAt: iso(r.next_retry_at),
      startedAt: iso(r.started_at),
      finishedAt: iso(r.finished_at),
      createdAt: iso(r.created_at) ?? "",
      updatedAt: iso(r.updated_at) ?? "",
    })),
    window
  );
}

export interface OcrSummary {
  /** OCR pages of the active version: the larger of the durable progress table and pages that carry OCR blocks. */
  pages: number;
  blocks: number;
}

export async function getOcrSummary(workspaceId: string, userId: string, sourceId: number): Promise<OcrSummary | null> {
  if (!isValidSourceId(sourceId)) return null;
  const rows = (await sql`
    SELECT
      (SELECT count(*) FROM "source_version_ocr_pages" o
        WHERE o."source_version_id" = f."active_version_id" AND o."workspace_id" = f."workspace_id") AS ocr_pages_done,
      (SELECT count(DISTINCT b."page") FROM "document_blocks" b
        WHERE b."source_version_id" = f."active_version_id" AND b."workspace_id" = f."workspace_id" AND b."ocr") AS ocr_block_pages,
      (SELECT count(*) FROM "document_blocks" b
        WHERE b."source_version_id" = f."active_version_id" AND b."workspace_id" = f."workspace_id" AND b."ocr") AS ocr_blocks
    FROM "files" f
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
  `) as Row[];
  const r = rows[0];
  if (!r) return null;
  return { pages: Math.max(Number(r.ocr_pages_done ?? 0), Number(r.ocr_block_pages ?? 0)), blocks: Number(r.ocr_blocks ?? 0) };
}

export interface ResolvedBlob {
  url: string;
  mimeType: string;
  fileName: string;
  versionNumber: number;
  /** Version whose bytes are served; differs from versionNumber for a duplicate (its own blob was deleted). */
  blobVersionNumber: number;
}

/**
 * Where the bytes of a version live. A duplicate version (T19) has no blob of its own: it is served from the version
 * named by `duplicate_of`. `versionNumber` omitted = the active version. A legacy first version keeps its blob URL on
 * the Source row (`files.storage_url`). null = unknown/foreign/deleted Source or no stored bytes.
 */
export async function resolveVersionBlob(
  workspaceId: string,
  userId: string,
  sourceId: number,
  versionNumber: number | null
): Promise<ResolvedBlob | null> {
  if (!isValidSourceId(sourceId)) return null;
  if (versionNumber !== null && !isValidSourceId(versionNumber)) return null;
  const rows = (await sql`
    SELECT f."name", f."type", f."storage_url", v."version_number", v."mime_type", v."upload_state",
           b."storage_key" AS blob_key, b."version_number" AS blob_version_number
    FROM "files" f
    INNER JOIN "source_versions" v
      ON v."source_id" = f."id" AND v."workspace_id" = f."workspace_id"
     AND ((${versionNumber}::int IS NULL AND v."id" = f."active_version_id") OR v."version_number" = ${versionNumber}::int)
    LEFT JOIN "source_versions" b ON b."id" = COALESCE(v."duplicate_of", v."id") AND b."workspace_id" = f."workspace_id"
    WHERE f."id" = ${sourceId}::int AND f."workspace_id" = ${workspaceId}::uuid
      AND f."user_id" = ${userId} AND f."deleted_at" IS NULL
  `) as Row[];
  const r = rows[0];
  if (!r || r.upload_state !== "stored" || r.blob_version_number === null || r.blob_version_number === undefined) return null;
  const key = typeof r.blob_key === "string" ? r.blob_key : "";
  const blobVersion = Number(r.blob_version_number);
  const url = /^https?:\/\//.test(key) ? key : blobVersion === 1 ? ((r.storage_url as string | null) ?? "") : "";
  if (!url) return null;
  return {
    url,
    mimeType: String(r.mime_type ?? r.type ?? "application/octet-stream"),
    fileName: String(r.name),
    versionNumber: Number(r.version_number),
    blobVersionNumber: blobVersion,
  };
}
