import { describe, expect, it } from "vitest";
import {
  DEFAULT_PAGE_SIZE,
  MAX_PAGE_SIZE,
  buildBlockTree,
  clampLimit,
  clampOffset,
  describePages,
  describeSpan,
  toPage,
  toWindow,
  type StructureBlock,
} from "@/lib/files/source-detail-paging";

const block = (i: number, headingPath: string[], over: Partial<StructureBlock> = {}): StructureBlock => ({
  id: `b${i}`,
  blockIndex: i,
  type: "paragraph",
  headingPath,
  page: 1,
  ocr: false,
  rowNumber: null,
  charStart: 0,
  charEnd: 5,
  preview: "text",
  textLength: 4,
  ...over,
});

describe("page bounds", () => {
  it("defaults to 50, caps at 200, floors at 1", () => {
    expect(clampLimit(undefined)).toBe(DEFAULT_PAGE_SIZE);
    expect(clampLimit(NaN)).toBe(50);
    expect(clampLimit("abc")).toBe(50);
    expect(clampLimit(0)).toBe(1);
    expect(clampLimit(-5)).toBe(1);
    expect(clampLimit(25.9)).toBe(25);
    expect(clampLimit(200)).toBe(200);
    expect(clampLimit(201)).toBe(MAX_PAGE_SIZE);
    expect(clampLimit(1e9)).toBe(200);
    expect(clampLimit(Infinity)).toBe(50);
  });

  it("never lets the offset go negative or non-finite", () => {
    expect(clampOffset(-10)).toBe(0);
    expect(clampOffset(NaN)).toBe(0);
    expect(clampOffset("7")).toBe(7);
    expect(clampOffset(12.8)).toBe(12);
  });

  it("builds a window and reports a next offset only when an extra row came back", () => {
    const w = toWindow({ limit: 2, offset: 4 });
    expect(toPage([1, 2, 3], w)).toEqual({ items: [1, 2], limit: 2, offset: 4, nextOffset: 6 });
    expect(toPage([1, 2], w)).toEqual({ items: [1, 2], limit: 2, offset: 4, nextOffset: null });
    expect(toWindow()).toEqual({ limit: 50, offset: 0 });
  });
});

describe("buildBlockTree", () => {
  it("nests blocks by heading path and starts a new section when the same title reappears elsewhere", () => {
    const tree = buildBlockTree([
      block(0, []),
      block(1, ["Intro"]),
      block(2, ["Intro", "Scope"]),
      block(3, ["Intro", "Scope"]),
      block(4, ["Intro"]),
      block(5, ["Terms"]),
      block(6, ["Terms", "Scope"]),
      block(7, ["Intro"]),
    ]);
    expect(tree.blocks.map((b) => b.blockIndex)).toEqual([0]);
    expect(tree.children.map((c) => c.title)).toEqual(["Intro", "Terms", "Intro"]);
    const intro = tree.children[0];
    expect(intro.depth).toBe(1);
    expect(intro.blocks.map((b) => b.blockIndex)).toEqual([1, 4]);
    expect(intro.children).toHaveLength(1);
    expect(intro.children[0].title).toBe("Scope");
    expect(intro.children[0].depth).toBe(2);
    expect(intro.children[0].blocks.map((b) => b.blockIndex)).toEqual([2, 3]);
    expect(tree.children[1].children[0].title).toBe("Scope");
  });

  it("creates every missing level for a block whose path skips levels", () => {
    const tree = buildBlockTree([block(0, ["A", "B", "C"])]);
    expect(tree.children[0].children[0].children[0].blocks).toHaveLength(1);
  });
});

describe("span descriptions", () => {
  it("never invents a page", () => {
    expect(describePages([])).toBeNull();
    expect(describePages([{ page: null, charStart: 1, charEnd: 2 }])).toBeNull();
    expect(describePages([{ page: 3, charStart: 0, charEnd: 1 }])).toBe("p. 3");
    expect(describePages([{ page: 5, charStart: 0, charEnd: 1 }, { page: 3, charStart: 0, charEnd: 1 }])).toBe("pp. 3-5");
    expect(describeSpan({ page: null, charStart: null, charEnd: null })).toBe("page unavailable");
    expect(describeSpan({ page: 2, charStart: 10, charEnd: 40 })).toBe("p. 2, chars 10-40");
  });
});
