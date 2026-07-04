import { beforeEach, describe, expect, it, vi } from "vitest";

const { getUserId, runQueuedSourceIntake } = vi.hoisted(() => ({
  getUserId: vi.fn(),
  runQueuedSourceIntake: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  getUserId,
  isNotAuthenticatedError: (error: unknown) =>
    error instanceof Error && error.message === "not_authenticated",
}));
vi.mock("@/lib/ingestion/source-intake", () => ({ runQueuedSourceIntake }));

import { POST } from "@/app/api/sources/process-now/route";

describe("user Source intake route", () => {
  beforeEach(() => {
    getUserId.mockReset();
    runQueuedSourceIntake.mockReset();
    getUserId.mockResolvedValue("user_123");
    runQueuedSourceIntake.mockResolvedValue({
      claimed: 1,
      ready: 1,
      retrying: 0,
      failed: 0,
    });
  });

  it("runs a single queued Source scoped to the signed-in user", async () => {
    const response = await POST(
      new Request("https://scribe.example/api/sources/process-now", {
        method: "POST",
        body: JSON.stringify({ sourceId: 42 }),
      })
    );

    expect(response.status).toBe(200);
    expect(runQueuedSourceIntake).toHaveBeenCalledWith(1, {
      userId: "user_123",
      sourceId: 42,
    });
  });

  it("rejects invalid Source ids before touching the queue", async () => {
    const response = await POST(
      new Request("https://scribe.example/api/sources/process-now", {
        method: "POST",
        body: JSON.stringify({ sourceId: "nope" }),
      })
    );

    expect(response.status).toBe(400);
    expect(runQueuedSourceIntake).not.toHaveBeenCalled();
  });
});
