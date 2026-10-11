import { describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ batches: [] as unknown[][] }));

vi.mock("@/lib/db-config", () => ({
  db: {
    execute: (q: unknown) => ({ kind: "execute", q }),
    batch: async (items: unknown[]) => {
      h.batches.push(items);
      return items.map((_, i) => [{ i }]);
    },
  },
}));

import { scoped } from "@/lib/scoped";

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
    expect(out).toEqual([[{ i: 1 }], [{ i: 2 }]]);
  });

  it("rejects ids that are not UUIDs", () => {
    expect(() => scoped("x'; drop table files;--")).toThrow(/UUID/);
  });
});
