/**
 * Test double for `@/lib/scoped`: `scoped(workspaceId).sql` records the statement text, its values and the workspace
 * it ran in; `batch` just awaits the queries. Use it from vi.mock factories:
 *   vi.mock("@/lib/scoped", async () => (await import("./support/scoped-mock")).scopedFake((text, values, ws) => mocks.sql(text, values, ws)));
 */
export function scopedFake(run: (text: string, values: unknown[], workspaceId: string) => unknown) {
  return {
    scoped: (workspaceId: string) => ({
      workspaceId,
      sql: async (strings: TemplateStringsArray, ...values: unknown[]) => run(strings.join("?"), values, workspaceId),
      batch: async (queries: PromiseLike<unknown>[]) => Promise.all(queries),
    }),
    dbErrorText: (error: unknown) => (error instanceof Error ? error.message : ""),
  };
}
