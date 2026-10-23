import { put } from "@vercel/blob/client";

type Fetcher = (input: string | URL | Request, init?: RequestInit) => Promise<Response>;
type Uploader = (input: { pathname: string; token: string; file: File }) => Promise<void>;
type EvidenceItem = { id: string; label: string; sourceId: number; sourceName: string };

export type SmokeStep = { name: string; ok: boolean; detail: string };

const fixtureText = "Scribe smoke fixture. The policy allows 17 days of annual leave.\n";

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
}): Promise<SmokeStep[]> {
  const base = new URL(options.baseUrl);
  const fetcher = options.fetcher ?? fetch;
  const steps: SmokeStep[] = [];
  const wait = options.wait ?? ((ms: number) => new Promise((resolve) => setTimeout(resolve, ms)));
  const maxAttempts = 4;
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
  if (!uploadReady || uploadedSourceId === null) {
    steps.push({ name: "ask question (chat UI message SSE)", ok: false, detail: "skipped after upload or queue kick failed" });
  } else await record("ask question (chat UI message SSE)", async () => {
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      const response = await appFetch(new URL("/api/chat", base), {
        method: "POST",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ messages: [{ id: "scribe-smoke-user", role: "user", parts: [{ type: "text", text: "How many days of annual leave does the smoke fixture allow? Cite the source." }] }] }),
      });
      if (!response.ok) throw new Error("chat failed");
      const parsed = parseChatSse(await response.text());
      const citations = [...parsed.answer.matchAll(/\[(E\d+)\]/g)].map((match) => match[1]);
      const sourceCitation = citations
        .map((label) => parsed.evidence.get(label))
        .find((item) => item?.sourceId === uploadedSourceId && item.sourceName === "scribe-smoke.txt");
      if (/\b17\b/.test(parsed.answer) && sourceCitation) {
        evidenceId = sourceCitation.id;
        return `answer states 17 and cites uploaded source (${sourceCitation.label})`;
      }
      if (attempt < maxAttempts) await wait(1000);
    }
    throw new Error("uploaded source did not become answerable with a matching citation");
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

  return steps;
}

function args(argv: string[]) {
  const result: Record<string, string> = {};
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    if (key === "--base-url" || key === "--session-token") result[key] = argv[++i] ?? "";
  }
  return result;
}

async function main() {
  const parsed = args(process.argv.slice(2));
  const baseUrl = parsed["--base-url"];
  const sessionToken = parsed["--session-token"] ?? process.env.SCRIBE_SESSION_TOKEN ?? "";
  if (!baseUrl || !sessionToken) {
    console.error("Usage: npx tsx scripts/smoke.ts --base-url URL --session-token TOKEN (or SCRIBE_SESSION_TOKEN)");
    process.exitCode = 2;
    return;
  }
  const steps = await runSmoke({ baseUrl, sessionToken });
  const width = Math.max(...steps.map((step) => step.name.length));
  console.log(`${"STEP".padEnd(width)}  RESULT  DETAIL`);
  for (const step of steps) console.log(`${step.name.padEnd(width)}  ${step.ok ? "PASS" : "FAIL"}    ${step.detail}`);
  if (steps.some((step) => !step.ok)) process.exitCode = 1;
}

if (process.argv[1]?.endsWith("scripts/smoke.ts")) {
  void main().catch(() => {
    console.error("Smoke failed before step execution.");
    process.exitCode = 1;
  });
}
