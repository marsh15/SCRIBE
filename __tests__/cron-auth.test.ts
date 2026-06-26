import { beforeEach, describe, expect, it, vi } from "vitest";

const { runQueuedSourceIntake } = vi.hoisted(() => ({
  runQueuedSourceIntake: vi.fn(),
}));

vi.mock("@/lib/env", () => ({ env: { CRON_SECRET: "cron-secret" } }));
vi.mock("@/lib/ingestion/source-intake", () => ({ runQueuedSourceIntake }));

import { GET } from "@/app/api/internal/ingest/run/route";

describe("Source intake cron interface", () => {
  beforeEach(() => {
    runQueuedSourceIntake.mockReset();
    runQueuedSourceIntake.mockResolvedValue({
      claimed: 0,
      ready: 0,
      retrying: 0,
      failed: 0,
    });
  });

  it("rejects requests without Vercel bearer authentication", async () => {
    const response = await GET(new Request("https://scribe.example/api/internal/ingest/run"));
    expect(response.status).toBe(401);
    expect(runQueuedSourceIntake).not.toHaveBeenCalled();
  });

  it("runs one queued Source for an authenticated cron request", async () => {
    const response = await GET(
      new Request("https://scribe.example/api/internal/ingest/run?limit=1", {
        headers: { authorization: "Bearer cron-secret" },
      })
    );
    expect(response.status).toBe(200);
    expect(runQueuedSourceIntake).toHaveBeenCalledWith(1);
  });
});
