import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { after, callIntelligence, afterCallbacks, envMock, purgeLegacy } = vi.hoisted(() => {
  const afterCallbacks: Array<() => Promise<void>> = [];
  return {
    afterCallbacks,
    after: vi.fn((cb: () => Promise<void>) => void afterCallbacks.push(cb)),
    callIntelligence: vi.fn(),
    envMock: {
      CRON_SECRET: "cron-secret",
      INTELLIGENCE_BASE_URL: "https://scribe.example",
      VERCEL_AUTOMATION_BYPASS_SECRET: undefined,
      INGEST_ENGINE: "python",
      INTERNAL_SERVICE_SECRET: "internal",
    } as Record<string, string | undefined>,
    purgeLegacy: vi.fn(),
  };
});

vi.mock("next/server", async () => {
  const actual = await vi.importActual<typeof import("next/server")>("next/server");
  return { ...actual, after };
});
vi.mock("@/lib/env", () => ({ env: envMock }));
vi.mock("@/lib/ingestion/source-delete", async () => {
  const actual = await vi.importActual<typeof import("@/lib/ingestion/source-delete")>("@/lib/ingestion/source-delete").catch(() => null);
  return { ...(actual ?? {}), purgeSoftDeletedSources: purgeLegacy, pythonEngineConfigured: () => envMock.INGEST_ENGINE === "python" && Boolean(envMock.INTERNAL_SERVICE_SECRET) };
});
vi.mock("@/lib/intelligence-client", () => ({
  callIntelligence,
  baseUrl: () => "https://scribe.example",
}));

import { POST as tick } from "@/app/api/internal/ingest/tick/route";
import { GET as sweep } from "@/app/api/internal/ingest/sweep/route";

const auth = { authorization: "Bearer cron-secret" };
const stepResponse = (body: object, status = 200) => new Response(JSON.stringify(body), { status });
async function drain() {
  while (afterCallbacks.length) await afterCallbacks.shift()!();
}

describe("ingest tick route", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    afterCallbacks.length = 0;
    after.mockClear();
    callIntelligence.mockReset();
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}", { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  it("rejects requests without the cron secret and does no work", async () => {
    const res = await tick(new Request("https://scribe.example/api/internal/ingest/tick", { method: "POST" }));
    expect(res.status).toBe(401);
    expect(after).not.toHaveBeenCalled();
    expect(callIntelligence).not.toHaveBeenCalled();
  });

  it("acknowledges with 202, runs one Python step in after(), and re-fires itself while work remains", async () => {
    callIntelligence.mockResolvedValue(stepResponse({ claimed: true, outcome: "yielded", more: true, job_id: 7 }));
    const res = await tick(new Request("https://scribe.example/api/internal/ingest/tick?hop=3", { method: "POST", headers: auth }));
    expect(res.status).toBe(202);
    expect(callIntelligence).not.toHaveBeenCalled(); // nothing runs before the response
    await drain();
    expect(callIntelligence).toHaveBeenCalledTimes(1);
    const [path, token] = callIntelligence.mock.calls[0];
    expect(path).toBe("/ingest/step");
    expect(token).toMatchObject({ scope: "ingest_step" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://scribe.example/api/internal/ingest/tick?hop=4");
    expect(init.headers.authorization).toBe("Bearer cron-secret");
  });

  it("stops the chain when no claimable work remains", async () => {
    callIntelligence.mockResolvedValue(stepResponse({ claimed: false, reason: "no_work", more: false }));
    await tick(new Request("https://scribe.example/api/internal/ingest/tick", { method: "POST", headers: auth }));
    await drain();
    expect(callIntelligence).toHaveBeenCalledTimes(1);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not re-fire when the Python step fails, and never throws out of after()", async () => {
    callIntelligence.mockResolvedValue(stepResponse({ error: "x" }, 503));
    vi.spyOn(console, "error").mockImplementation(() => {});
    await tick(new Request("https://scribe.example/api/internal/ingest/tick", { method: "POST", headers: auth }));
    await expect(drain()).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("refuses to run past the hop limit", async () => {
    const res = await tick(new Request("https://scribe.example/api/internal/ingest/tick?hop=500", { method: "POST", headers: auth }));
    expect((await res.json()).accepted).toBe(false);
    expect(after).not.toHaveBeenCalled();
  });
});

describe("ingest sweep route", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    afterCallbacks.length = 0;
    callIntelligence.mockReset();
    fetchMock.mockReset();
    fetchMock.mockResolvedValue(new Response("{}", { status: 202 }));
    vi.stubGlobal("fetch", fetchMock);
  });

  it("requires the cron secret", async () => {
    expect((await sweep(new Request("https://scribe.example/api/internal/ingest/sweep"))).status).toBe(401);
    expect(callIntelligence).not.toHaveBeenCalled();
  });

  it("requeues expired leases through Python and kicks a tick when work is claimable", async () => {
    callIntelligence.mockImplementation(async (path: string) =>
      path === "/ingest/purge"
        ? stepResponse({ listed: 0, purged: 0, retry_scheduled: 0 })
        : stepResponse({ swept: 2, job_ids: [1, 2], more: true })
    );
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(await res.json()).toMatchObject({ ok: true, swept: 2, more: true });
    expect(callIntelligence.mock.calls[0][0]).toBe("/ingest/sweep");
    await drain();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("also purges soft-deleted Sources (T20) and reports the outcome", async () => {
    callIntelligence.mockImplementation(async (path: string) =>
      path === "/ingest/purge"
        ? stepResponse({ listed: 3, purged: 2, retry_scheduled: 1 })
        : stepResponse({ swept: 0, more: false })
    );
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(await res.json()).toMatchObject({ ok: true, purge: { listed: 3, purged: 2, retryScheduled: 1 } });
    expect(callIntelligence.mock.calls.map((c) => c[0])).toEqual(["/ingest/sweep", "/ingest/purge"]);
  });

  it("a failing purge does not fail the sweep", async () => {
    callIntelligence.mockImplementation(async (path: string) =>
      path === "/ingest/purge" ? stepResponse({}, 503) : stepResponse({ swept: 1, more: false })
    );
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, swept: 1, purge: { error: "purge_failed" } });
  });
});

describe("ingest sweep route, Python engine not configured (P0-3)", () => {
  beforeEach(() => {
    callIntelligence.mockReset();
    purgeLegacy.mockReset();
    envMock.INGEST_ENGINE = "legacy";
  });
  afterEach(() => {
    envMock.INGEST_ENGINE = "python";
    envMock.INTERNAL_SERVICE_SECRET = "internal";
  });

  it("answers 200 with a note, never calls Python, and purges leftover soft-deleted rows", async () => {
    purgeLegacy.mockResolvedValue({ listed: 2, purged: 2, blobFailed: 0 });
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      ok: true,
      swept: 0,
      more: false,
      note: "python engine not configured",
      purge: { listed: 2, purged: 2 },
    });
    expect(callIntelligence).not.toHaveBeenCalled();
    expect(purgeLegacy).toHaveBeenCalledTimes(1);
  });

  it("is also legacy-mode when INTERNAL_SERVICE_SECRET is missing even if INGEST_ENGINE=python", async () => {
    envMock.INGEST_ENGINE = "python";
    envMock.INTERNAL_SERVICE_SECRET = undefined;
    purgeLegacy.mockResolvedValue({ listed: 0, purged: 0, blobFailed: 0 });
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(res.status).toBe(200);
    expect(callIntelligence).not.toHaveBeenCalled();
  });

  it("a failing legacy cleanup still answers 200", async () => {
    purgeLegacy.mockRejectedValue(new Error("db down"));
    const res = await sweep(new Request("https://scribe.example/api/internal/ingest/sweep", { headers: auth }));
    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({ ok: true, purge: { error: "purge_failed" } });
  });
});
