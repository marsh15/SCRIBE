/** Pure helpers for the Source detail page (T24): bounded paging and the block tree. No I/O, no document text logging. */

export const DEFAULT_PAGE_SIZE = 50;
export const MAX_PAGE_SIZE = 200;

export interface PageRequest {
  limit?: number;
  offset?: number;
}

export interface PageWindow {
  limit: number;
  offset: number;
}

/** Page size is decided here, never by the client: default 50, at least 1, at most 200. Non-finite input -> default. */
export function clampLimit(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return DEFAULT_PAGE_SIZE;
  return Math.min(MAX_PAGE_SIZE, Math.max(1, Math.floor(n)));
}

export function clampOffset(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.floor(n));
}

export function toWindow(request?: PageRequest): PageWindow {
  return { limit: clampLimit(request?.limit), offset: clampOffset(request?.offset) };
}

export interface Page<T> {
  items: T[];
  limit: number;
  offset: number;
  /** Offset to request next, or null when this is the last page. */
  nextOffset: number | null;
}

/** Queries fetch `limit + 1` rows; the extra row only proves another page exists and is dropped here. */
export function toPage<T>(rows: T[], window: PageWindow): Page<T> {
  const hasMore = rows.length > window.limit;
  const items = hasMore ? rows.slice(0, window.limit) : rows;
  return { items, limit: window.limit, offset: window.offset, nextOffset: hasMore ? window.offset + window.limit : null };
}

export const emptyPage = <T,>(window: PageWindow): Page<T> => ({ items: [], limit: window.limit, offset: window.offset, nextOffset: null });

export function isValidSourceId(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

// ---------------------------------------------------------------------------------------------- block tree

export interface StructureBlock {
  id: string;
  blockIndex: number;
  type: string;
  headingPath: string[];
  page: number | null;
  ocr: boolean;
  rowNumber: number | null;
  charStart: number | null;
  charEnd: number | null;
  /** Bounded preview of the block text (the server truncates). */
  preview: string;
  textLength: number;
}

export interface BlockTreeNode {
  /** Heading text of this level. The synthetic root has an empty title. */
  title: string;
  depth: number;
  blocks: StructureBlock[];
  children: BlockTreeNode[];
}

/**
 * Nest blocks by heading path: a block whose path is ["A", "B"] sits in section B inside section A. Blocks arrive in
 * document order, so a section node is reused only while the path prefix is unchanged (the same heading text appearing
 * twice in different places yields two sections).
 */
export function buildBlockTree(blocks: StructureBlock[]): BlockTreeNode {
  const root: BlockTreeNode = { title: "", depth: 0, blocks: [], children: [] };
  let stack: BlockTreeNode[] = [root];
  for (const block of blocks) {
    const path = block.headingPath;
    // keep the longest prefix of the open sections that still matches this block's path
    let keep = 1;
    while (keep < stack.length && keep <= path.length && stack[keep].title === path[keep - 1]) keep++;
    stack = stack.slice(0, keep);
    for (let level = keep - 1; level < path.length; level++) {
      const node: BlockTreeNode = { title: path[level], depth: level + 1, blocks: [], children: [] };
      stack[stack.length - 1].children.push(node);
      stack.push(node);
    }
    stack[stack.length - 1].blocks.push(block);
  }
  return root;
}

export interface SpanView {
  page: number | null;
  charStart: number | null;
  charEnd: number | null;
}

/** "p. 3" / "pp. 3-5" / null from the pages the spans touch (spans are exact, never estimated: INV-7). */
export function describePages(spans: SpanView[]): string | null {
  const pages = spans.map((s) => s.page).filter((p): p is number => typeof p === "number");
  if (pages.length === 0) return null;
  const lo = Math.min(...pages);
  const hi = Math.max(...pages);
  return lo === hi ? `p. ${lo}` : `pp. ${lo}-${hi}`;
}

export function describeSpan(span: SpanView): string {
  const where = span.page === null ? "page unavailable" : `p. ${span.page}`;
  const range = span.charStart === null || span.charEnd === null ? "" : `, chars ${span.charStart}-${span.charEnd}`;
  return `${where}${range}`;
}
