import { beforeEach, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ rows: [] as unknown[], query: vi.fn() }));
vi.mock("@/lib/db-config", () => ({
  sql: (strings: TemplateStringsArray, ...values: unknown[]) => h.query(strings.join("?"), values),
}));

import { requeueFailedBackfillJob } from "@/lib/ingestion/python-queue";

beforeEach(() => {
  h.query.mockReset();
  h.query.mockResolvedValue([]);
});

it("retries only the current, non-deleted desired version's failed structural generation", async () => {
  h.query.mockResolvedValue([{ id: 7 }]);
  expect(await requeueFailedBackfillJob(42)).toBe(true);
  expect(h.query).toHaveBeenCalledTimes(1);
  const [query, values] = h.query.mock.calls[0];
  expect(query).toContain("profile.\"name\" = 'structural-v1'");
  expect(query).toContain('job."source_version_id" = source."active_version_id"');
  expect(query).toContain('source."desired_version_id" = source."active_version_id"');
  expect(query).toContain('source."deleted_at" IS NULL');
  expect(query).toContain('source."status" = \'ready\'');
  expect(query).toContain('job."status" = \'failed\'');
  expect(query).not.toContain('UPDATE "files"');
  expect(values).toEqual([42]);
});

it("reports no retry when there is no eligible failed backfill job", async () => {
  expect(await requeueFailedBackfillJob(42)).toBe(false);
});
