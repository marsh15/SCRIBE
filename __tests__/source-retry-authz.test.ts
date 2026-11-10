import { beforeEach, describe, expect, it, vi } from "vitest";

const m = vi.hoisted(() => ({ resolve: vi.fn(), requeue: vi.fn(), fireTick: vi.fn(), env: { INGEST_ENGINE: "python" } as Record<string, string> }));
vi.mock("@/lib/workspace", () => ({ resolveWorkspace: m.resolve }));
vi.mock("@/lib/ingestion/python-queue", () => ({ requeueFailedSource: m.requeue }));
vi.mock("@/lib/ingestion/tick", () => ({ fireTick: m.fireTick }));
vi.mock("@/lib/env", () => ({ env: m.env }));
vi.mock("@/lib/db-config", () => ({ db: {} }));
vi.mock("@/lib/scoped", () => ({ scoped: () => ({ batch: async () => [] }) }));
vi.mock("@/lib/ingestion/source-intake", () => ({ deleteSource: vi.fn() }));
vi.mock("@/lib/ingestion/source-versions", () => ({ listSourceVersions: vi.fn() }));
vi.mock("next/cache", () => ({ revalidatePath: vi.fn() }));
vi.mock("next/server", () => ({ after: (fn: () => void) => void fn() }));

import { retrySource } from "@/app/files/actions";

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, "error").mockImplementation(() => {});
  m.resolve.mockResolvedValue({ userId: "user_1", workspaceId: "22222222-2222-4222-8222-222222222222" });
});

describe("retry is authorized by the server-derived workspace only", () => {
  it("requeues in the caller's workspace and kicks the tick chain on success", async () => {
    m.requeue.mockResolvedValue(true);
    expect(await retrySource(42)).toEqual({ success: true });
    expect(m.requeue).toHaveBeenCalledWith("22222222-2222-4222-8222-222222222222", 42);
    expect(m.fireTick).toHaveBeenCalled();
  });

  it("refuses a Source that is not failed, unknown or foreign without kicking anything", async () => {
    m.requeue.mockResolvedValue(false);
    expect(await retrySource(42)).toEqual({ success: false, error: "Only failed Sources can be retried" });
    expect(m.fireTick).not.toHaveBeenCalled();
  });

  it("fails closed when the caller is not authenticated: nothing is requeued", async () => {
    m.resolve.mockRejectedValue(new Error("Not authenticated"));
    expect(await retrySource(42)).toEqual({ success: false, error: "Could not retry this Source" });
    expect(m.requeue).not.toHaveBeenCalled();
    expect(m.fireTick).not.toHaveBeenCalled();
  });
});
