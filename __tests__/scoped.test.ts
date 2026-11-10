import { describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ batches: [] as unknown[][] }));

vi.mock("@/lib/db-config", () => ({
  db: {
    execute: (q: unknown) => ({ kind: "execute", q }),
    batch: async (items: unknown[]) => {
      h.batches.push(items);
      return items.map((_, i) => Object.assign([{ i }], { rows: [{ i }] }));
    },
  },
}));

import { dbErrorText, scoped } from "@/lib/scoped";

const WID = "0b9c3a52-5d1e-4f0a-9a43-2f6f1a7c8e10";

describe("scoped()", () => {
  it("prepends the transaction-local context and strips its result", async () => {
    const out = await scoped(WID).batch([{ kind: "q1" } as never, { kind: "q2" } as never]);
    expect(h.batches).toHaveLength(1);
    expect(h.batches[0]).toHaveLength(3);
    const first = h.batches[0][0] as { kind: string; q: { queryChunks: unknown[] } };
    expect(first.kind).toBe("execute");
    const text = JSON.stringify(first.q);
    expect(text).toContain("set_config('app.workspace_id'");
    expect(text).toContain(", true)");
    expect(text).toContain(WID);
    expect(out.map((r) => [...r])).toEqual([[{ i: 1 }], [{ i: 2 }]]);
  });

  it("rejects ids that are not UUIDs", () => {
    expect(() => scoped("x'; drop table files;--")).toThrow(/UUID/);
  });

  it("scoped().sql runs one raw statement in the same batch as the context and returns its rows", async () => {
    const rows = await scoped(WID).sql<{ i: number }>`select ${1}::int as i`;
    const batch = h.batches.at(-1) as Array<{ kind: string }>;
    expect(batch).toHaveLength(2);
    expect(JSON.stringify(batch[0])).toContain("set_config('app.workspace_id'");
    expect(rows).toEqual([{ i: 1 }]);
  });
});

describe("dbErrorText", () => {
  it("includes the driver message that Drizzle keeps on the cause", () => {
    const inner = new Error('SOURCE_NOT_FOUND');
    const outer = Object.assign(new Error("Failed query: select ..."), { cause: inner });
    expect(dbErrorText(outer)).toContain("SOURCE_NOT_FOUND");
    expect(dbErrorText("not an error")).toBe("");
  });
});
