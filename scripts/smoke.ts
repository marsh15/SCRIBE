import { put } from "@vercel/blob/client";

type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;
type Uploader = (input: { pathname: string; token: string; file: File }) => Promise<void>;
type EvidenceItem = { id: string; label: string; sourceId: number; sourceName: string };

export type SmokeStep = { name: string; ok: boolean; detail: string; skipped?: boolean };

const fixtureText = "Scribe smoke fixture. The policy allows 17 days of annual leave.\n";
const updatedFixtureText = "Scribe smoke fixture. The policy allows 21 days of annual leave.\n";
const QUESTION = "How many days of annual leave does the smoke fixture allow? Cite the source.";

function appendCookie(headers: Headers, token: string) {
  headers.set("cookie", `__session=${token}`);
}

async function checkedJson(response: Response) {
  if (!response.ok) throw new Error("request failed");
  return response.json() as Promise<Record<string, unknown>>;
}

function parseChatSse(sse: string) {
  let answer = "";
  const evidence = new Map<string, EvidenceItem>();
  for (const line of sse.split(/\r?\n/)) {
    if (!line.startsWith("data: ") || line === "data: [DONE]") continue;
    try {
      const event = JSON.parse(line.slice(6)) as Record<string, unknown>;
      if (event.type === "text-delta" && typeof event.delta === "string") answer += event.delta;
      const candidates = [event.output, event.result, event];
      for (const candidate of candidates) {
        if (!candidate || typeof candidate !== "object") continue;
        const result = candidate as { type?: string; evidence?: { items?: Array<{
          id?: unknown; label?: unknown; source_id?: unknown; sourceId?: unknown; source_name?: unknown; sourceName?: unknown;
        }> } };
        if (result.type !== "rag_search_result") continue;
        for (const item of result.evidence?.items ?? []) {
          const id = item.id;
          const label = item.label;
          const sourceId = item.source_id ?? item.sourceId;
          const sourceName = item.source_name ?? item.sourceName;
          if (typeof id === "string" && typeof label === "string" && typeof sourceId === "number" && typeof sourceName === "string") {
            evidence.set(label, { id, label, sourceId, sourceName });
          }
        }
      }
    } catch {
      // Ignore malformed or non-JSON SSE data without exposing response content.
    }
  }
  return { answer, evidence };
}

/** The callable flow is injected so tests exercise orchestration without Blob or provider traffic. */
export async function runSmoke(options: {
  baseUrl: string;
  sessionToken: string;
  fetcher?: Fetcher;
  uploader?: Uploader;
  wait?: (ms: number) => Promise<void>;
  /** Also exercise update (a second version) and delete. Needs INGEST_ENGINE=python for the update step. */
  lifecycle?: boolean;
  /** Readiness polls per question (default 4 for a local run; a preview that indexes through the tick chain needs more). */
  attempts?: number;
  waitMs?: number;
}): Promise<SmokeStep[]> {
  const base = new URL(options.baseUrl);
  const fetcher = options.fetcher ?? fetch;
  const steps: SmokeStep[] = [];
  const wait = options.wait ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  const maxAttempts = options.attempts ?? 4;
  const waitMs = options.waitMs ?? 1000;
  let uploadedSourceId: number | null = null;
  let uploadReady = false;
  const record = async (name: string, run: () => Promise<string>) => {
    try {
      steps.push({ name, ok: true, detail: await run() });
    } catch {
      steps.push({ name, ok: false, detail: "failed" });
    }
  };
  const appFetch: Fetcher = (input, init = {}) => {
    const target = new URL(input instanceof Request ? input.url : input.toString(), base);
    if (target.origin !== base.origin) throw new Error("cross-origin app request refused");
    const headers = new Headers(init.headers ?? (input instanceof Request ? input.headers : undefined));
    appendCookie(headers, options.sessionToken);
    return fetcher(target, { ...init, headers });
  };

  await record("Next health", async () => {
    const response = await fetcher(new URL("/api/health", base));
    const body = await checkedJson(response);
    if (body.ok !== true) throw new Error("health unavailable");
    return "200 OK";
  });

  await record("Python health auth guard / reachability (expected 401)", async () => {
    const response = await fetcher(new URL("/api/py/health", base));
    if (response.status !== 401) throw new Error("expected auth guard");
    return "expected 401; confirms Python route auth boundary/reachability, not authorized deep health";
  });

  await record("reserve, upload fixture, kick ingestion", async () => {
    const file = new File([fixtureText], "scribe-smoke.txt", { type: "text/plain" });
    const headers = new Headers({ "content-type": "application/json" });
    appendCookie(headers, options.sessionToken);
    const response = await fetcher(new URL("/api/sources/reserve", base), {
      method: "POST", headers,
      body: JSON.stringify({ name: file.name, size: file.size, type: file.type }),
    });
    const reservation = await checkedJson(response) as {
      source?: { id?: number }; uploadPath?: string; clientToken?: string;
    };
    if (!reservation.source?.id || !reservation.uploadPath || !reservation.clientToken) throw new Error("bad reservation");
    uploadedSourceId = reservation.source.id;
    if (options.uploader) {
      await options.uploader({ pathname: reservation.uploadPath, token: reservation.clientToken, file });
    } else {
      // This follows the same client-token Blob upload used by useSourceUpload; no Clerk cookie is sent to Blob.
      await put(reservation.uploadPath, file, {
        access: "private", token: reservation.clientToken, contentType: file.type,
      });
    }
    const kickHeaders = new Headers({ "content-type": "application/json" });
    appendCookie(kickHeaders, options.sessionToken);
    const kick = await fetcher(new URL("/api/sources/process-now", base), {
      method: "POST", headers: kickHeaders, body: JSON.stringify({ sourceId: reservation.source.id }),
    });
    const kicked = await checkedJson(kick);
    if (kicked.ok !== true) throw new Error("ingestion kick failed");
    uploadReady = true;
    return `source ${reservation.source.id} uploaded`;
  });

  let evidenceId: string | null = null;
  /** Ask until the answer matches `accept`; returns the cited evidence item of the uploaded source. */
  const askUntil = async (accept: (answer: string) => boolean, describe: string) => {
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const response = await appFetch(new URL("/api/chat", base), {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ messages: [{ id: "scribe-smoke-user", role: "user", parts: [{ type: "text", text: QUESTION }] }] }),
      });
      if (!response.ok) throw new Error("chat failed");
      const parsed = parseChatSse(await response.text());
      const citations = [...parsed.answer.matchAll(/\[(E\d+)\]/g)].map((match) => match[1]);
      const sourceCitation = citations
        .map((label) => parsed.evidence.get(label))
        .find((item) => item?.sourceId === uploadedSourceId && item.sourceName === "scribe-smoke.txt");
      if (accept(parsed.answer) && sourceCitation) return { item: sourceCitation, detail: `${describe} (${sourceCitation.label})` };
      if (attempt < maxAttempts) await wait(waitMs);
    }
    throw new Error("uploaded source did not become answerable with a matching citation");
  };
  if (!uploadReady || uploadedSourceId === null) {
    steps.push({ name: "ask question (chat UI message SSE)", ok: false, detail: "skipped after upload or queue kick failed" });
  } else await record("ask question (chat UI message SSE)", async () => {
    const { item, detail } = await askUntil((answer) => /\b17\b/.test(answer), "answer states 17 and cites uploaded source");
    evidenceId = item.id;
    return detail;
  });

  await record("open returned citation", async () => {
    if (!uploadReady || !evidenceId || uploadedSourceId === null) throw new Error("citation unavailable");
    const response = await appFetch(new URL(`/api/evidence/${encodeURIComponent(evidenceId)}`, base));
    const body = await checkedJson(response);
    if (body.itemId !== evidenceId || body.sourceDeleted !== false || body.sourceId !== uploadedSourceId || typeof body.quote !== "string" || !/\b17 days\b/i.test(body.quote)) {
      throw new Error("opened evidence did not match fixture");
    }
    return "citation opened";
  });


  /** Update (a second version while the first stays searchable) and delete, in the order a user would do them. */
  const lifecycle = async () => {
    const sourceId = uploadedSourceId;
    if (!uploadReady || sourceId === null) {
      for (const name of ["update: upload version 2", "update: answer reflects version 2", "update: citation opens at version 2",
        "delete: remove the Source", "delete: citation withheld", "delete: answers no longer cite it"]) {
        steps.push({ name, ok: false, detail: "skipped after upload failed" });
      }
      return;
    }
    let newVersion: number | null = null;
    let updateSkipped = false;
    const updatedFile = new File([updatedFixtureText], "scribe-smoke.txt", { type: "text/plain" });
    await record("update: upload version 2", async () => {
      const headers = new Headers({ "content-type": "application/json" });
      appendCookie(headers, options.sessionToken);
      const response = await fetcher(new URL(`/api/sources/${sourceId}/versions`, base), {
        method: "POST", headers,
        body: JSON.stringify({ name: updatedFile.name, size: updatedFile.size, type: updatedFile.type }),
      });
      if (response.status === 409) {
        // only the engine flag is a legitimate skip; "no_active_version" and other conflicts are real failures
        const body = (await response.json().catch(() => ({}))) as { code?: string };
        if (body.code !== "engine_unsupported") throw new Error("version reservation conflict");
        updateSkipped = true;
        return "SKIPPED: versioned uploads need INGEST_ENGINE=python on this deployment";
      }
      const reservation = await checkedJson(response) as {
        version?: { versionNumber?: number }; uploadPath?: string; clientToken?: string;
      };
      if (!reservation.version?.versionNumber || !reservation.uploadPath || !reservation.clientToken) throw new Error("bad reservation");
      newVersion = reservation.version.versionNumber;
      if (options.uploader) {
        await options.uploader({ pathname: reservation.uploadPath, token: reservation.clientToken, file: updatedFile });
      } else {
        await put(reservation.uploadPath, updatedFile, { access: "private", token: reservation.clientToken, contentType: updatedFile.type });
      }
      const kickHeaders = new Headers({ "content-type": "application/json" });
      appendCookie(kickHeaders, options.sessionToken);
      const kick = await fetcher(new URL("/api/sources/process-now", base), {
        method: "POST", headers: kickHeaders, body: JSON.stringify({ sourceId }),
      });
      if ((await checkedJson(kick)).ok !== true) throw new Error("ingestion kick failed");
      return `version ${newVersion} uploaded`;
    });
    if (steps.at(-1)?.detail.startsWith("SKIPPED")) Object.assign(steps.at(-1)!, { skipped: true, ok: false }); // a skip is never a pass

    let newEvidenceId: string | null = null;
    if (updateSkipped || newVersion === null) {
      const detail = updateSkipped ? "SKIPPED: no version 2 was uploaded" : "skipped after the upload failed";
      steps.push({ name: "update: answer reflects version 2", ok: false, detail, skipped: updateSkipped });
      steps.push({ name: "update: citation opens at version 2", ok: false, detail, skipped: updateSkipped });
    } else {
      await record("update: answer reflects version 2", async () => {
        const { item, detail } = await askUntil(
          (answer) => /\b21\b/.test(answer) && !/\b17\b/.test(answer),
          "answer states 21, not 17, and cites the Source"
        );
        newEvidenceId = item.id;
        return detail;
      });
      await record("update: citation opens at version 2", async () => {
        if (!newEvidenceId) throw new Error("no citation");
        const body = await checkedJson(await appFetch(new URL(`/api/evidence/${encodeURIComponent(newEvidenceId)}`, base)));
        if (body.sourceDeleted !== false || body.sourceId !== sourceId || body.versionNumber !== newVersion ||
          typeof body.quote !== "string" || !/\b21 days\b/i.test(body.quote)) throw new Error("opened evidence is not version 2");
        return `citation opens at version ${newVersion}`;
      });
    }

    await record("delete: remove the Source", async () => {
      // the citation must open BEFORE the delete, or "withheld" afterwards would prove nothing
      const target = newEvidenceId ?? evidenceId;
      if (!target) throw new Error("no citation to check before deleting");
      const before = await checkedJson(await appFetch(new URL(`/api/evidence/${encodeURIComponent(target)}`, base)));
      if (before.sourceDeleted !== false || typeof before.quote !== "string") throw new Error("citation did not open before the delete");
      const headers = new Headers();
      appendCookie(headers, options.sessionToken);
      const response = await fetcher(new URL(`/api/sources/${sourceId}`, base), { method: "DELETE", headers });
      if ((await checkedJson(response)).ok !== true) throw new Error("delete failed");
      return `source ${sourceId} deleted`;
    });
    await record("delete: citation withheld", async () => {
      const target = newEvidenceId ?? evidenceId;
      if (!target) throw new Error("no citation to reopen");
      const response = await appFetch(new URL(`/api/evidence/${encodeURIComponent(target)}`, base));
      if (response.status === 404) return "citation answers 404";
      const body = await checkedJson(response);
      if (body.sourceDeleted !== true || "quote" in body) throw new Error("deleted Source still exposes its quote");
      return "citation reports the Source as deleted, quote withheld";
    });
    await record("delete: answers no longer cite it", async () => {
      const response = await appFetch(new URL("/api/chat", base), {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ messages: [{ id: "scribe-smoke-user-2", role: "user", parts: [{ type: "text", text: QUESTION }] }] }),
      });
      if (!response.ok) throw new Error("chat failed");
      const parsed = parseChatSse(await response.text());
      if ([...parsed.evidence.values()].some((item) => item.sourceId === sourceId)) throw new Error("deleted Source is still cited");
      return "no answer evidence comes from the deleted Source";
    });
  };

  if (options.lifecycle) await lifecycle();
  return steps;
}

function args(argv: string[]) {
  const result: Record<string, string> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--base-url" || key === "--session-token" || key === "--attempts" || key === "--wait-ms") result[key] = argv[++i] ?? "";
    else if (key === "--lifecycle" || key === "--allow-skip") result[key] = "1";
  }
  return result;
}

async function main() {
  const parsed = args(process.argv.slice(2));
  const baseUrl = parsed["--base-url"];
  const sessionToken = parsed["--session-token"] ?? process.env.SCRIBE_SESSION_TOKEN ?? "";
  if (!baseUrl || !sessionToken) {
    console.error("Usage: npx tsx scripts/smoke.ts --base-url URL --session-token TOKEN (or SCRIBE_SESSION_TOKEN) [--lifecycle] [--allow-skip] [--attempts N] [--wait-ms MS]");
    process.exitCode = 2;
    return;
  }
  const attempts = parsed["--attempts"] ? Number(parsed["--attempts"]) : undefined;
  const waitMs = parsed["--wait-ms"] ? Number(parsed["--wait-ms"]) : undefined;
  const steps = await runSmoke({ baseUrl, sessionToken, lifecycle: parsed["--lifecycle"] === "1", attempts, waitMs });
  const width = Math.max(...steps.map((step) => step.name.length));
  console.log(`${"STEP".padEnd(width)}  RESULT  DETAIL`);
  for (const step of steps) console.log(`${step.name.padEnd(width)}  ${step.skipped ? "SKIP" : step.ok ? "PASS" : "FAIL"}    ${step.detail}`);
  const skipped = steps.some((step) => step.skipped);
  if (skipped) console.log("\nSKIPPED steps were NOT verified. Do not count them as passed.");
  if (steps.some((step) => !step.ok && !step.skipped)) process.exitCode = 1;
  else if (skipped && parsed["--allow-skip"] !== "1") {
    console.log("Exiting 3 because steps were skipped; pass --allow-skip once you have decided that is acceptable.");
    process.exitCode = 3;
  }
}

if (process.argv[1]?.endsWith("scripts/smoke.ts")) {
  void main().catch(() => {
    console.error("Smoke failed before step execution.");
    process.exitCode = 1;
  });
}
