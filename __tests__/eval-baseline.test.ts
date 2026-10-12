import { execFileSync, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  EMBEDDING_DIMENSIONS,
  EVAL_OWNER_ID,
  EmbeddingCache,
  assertEvalWorkspace,
  assertNoPersonalData,
  assertSafeConfig,
  buildRankingLine,
  buildSearchQuery,
  embeddingCacheKey,
  fakeEmbedding,
  inlineParams,
  installEmbeddingShim,
  isCorpusDocument,
  parseArgs,
  planCorpus,
  resolveOutDir,
  rowToRankedItem,
  run,
  sqlLiteral,
  summarizePlans,
  type EmbeddingStats,
} from "../scripts/eval-baseline";
import { sourceMimeTypeForName } from "../lib/ingestion/source-intake-policy";
import { EMBEDDING_MODEL_ID } from "../lib/embedding-config";

// Records the query lib/search.ts really builds: db-config's `db` is replaced by a connection-less drizzle
// neon-http instance whose final `.limit()` result captures toSQL() instead of executing.
const captured = vi.hoisted(() => ({ query: null as null | { sql: string; params: unknown[] } }));
vi.mock("@/lib/db-config", async () => {
  const { drizzle } = await import("drizzle-orm/neon-http");
  const schema = await import("@/lib/db-schema");
  const real = drizzle.mock({ schema });
  const wrap = (builder: any): any =>
    new Proxy(builder, {
      get(target, prop) {
        if (prop === "limit") {
          return (n: number) => {
            const q = target.limit(n);
            return {
              then(resolve: (rows: unknown[]) => void) {
                captured.query = q.toSQL();
                resolve([]);
              },
            };
          };
        }
        const value = target[prop];
        return typeof value === "function" ? (...a: unknown[]) => wrap(value.apply(target, a)) : value;
      },
    });
  return { db: { select: (fields: unknown) => wrap(real.select(fields as any)) } };
});
// pdf-parse's index.js runs a debug routine (reads ./test/data/...) when `module.parent` is unset, as under vitest.
// Point it at the same parser entry the package wraps; behaviour is identical.
vi.mock("pdf-parse", async () => {
  // @ts-expect-error no types for the deep import
  const mod = await import("pdf-parse/lib/pdf-parse.js");
  return { default: mod.default ?? mod };
});
vi.mock("@/lib/embeddings", () => ({
  generateEmbedding: async () => [0.25, -0.5, 0.125],
}));

const ROOT = path.resolve(__dirname, "..");

describe("psql SQL equals the SQL lib/search.ts builds", () => {
  beforeEach(() => {
    captured.query = null;
  });

  for (const threshold of [0.3, -1]) {
    it(`threshold ${threshold}: toSQL() of the script builder == recorded searchDocuments query`, async () => {
      const { searchDocuments } = await import("../lib/search");
      const res = await searchDocuments("what is the badge time?", EVAL_OWNER_ID, "11111111-2222-3333-4444-555555555555", 10, threshold);
      expect(res.documents).toEqual([]);
      const recorded = captured.query!;
      expect(recorded).not.toBeNull();

      const mine = buildSearchQuery(
        [0.25, -0.5, 0.125],
        EVAL_OWNER_ID,
        "11111111-2222-3333-4444-555555555555",
        10,
        threshold
      ).toSQL();
      expect(mine.sql).toBe(recorded.sql);
      expect(mine.params).toEqual(recorded.params);
      // sanity: it is the cosine query with the user + workspace scope and the threshold
      expect(recorded.sql).toContain("<=>");
      expect(recorded.sql).toContain('"files"."workspace_id" = $');
      expect(recorded.sql).toContain('"files"."user_id" = $');
      expect(recorded.sql).toMatch(/order by .* desc limit \$\d+$/);
      expect(recorded.params).toContain(threshold);
    });
  }

  it("inlining parameters leaves no placeholders and quotes strings safely", () => {
    const q = buildSearchQuery([0.1, 0.2], "o'brien", "ws", 10, 0.3).toSQL();
    const text = inlineParams(q.sql, q.params);
    expect(text).not.toMatch(/\$\d/);
    expect(text).toContain("'o''brien'");
    expect(text).toContain("'[0.1,0.2]'");
    expect(sqlLiteral(null)).toBe("NULL");
    expect(sqlLiteral(0.3)).toBe("0.3");
    expect(sqlLiteral({ a: "x'y" })).toBe(`'{"a":"x''y"}'`);
    expect(() => inlineParams("select $2", ["a"])).toThrow(/\$2/);
  });
});

describe("ranking lines", () => {
  it("has the rankings-format fields and the dense-legacy method", () => {
    const item = rowToRankedItem([42, "chunk text", { chunkIndex: 0 }, 7, "onboarding-handbook.pdf", "application/pdf", "0.83"]);
    expect(item).toEqual({
      chunk_id: "42",
      doc: "onboarding-handbook.pdf",
      text: "chunk text",
      score: 0.83,
      method: "dense-legacy",
    });
    const line = buildRankingLine({
      caseId: "sf-01",
      items: [item],
      latencyMs: 123.456,
      embeddingCalls: 1,
      cacheHits: 0,
      variant: "threshold-0.3",
      threshold: 0.3,
      driver: "psql",
      fake: false,
    });
    expect(Object.keys(line).sort()).toEqual(["case_id", "cost", "degraded", "latency_ms", "meta", "ranking"]);
    expect(line.latency_ms).toBe(123.5);
    expect(line.cost).toEqual({ embedding_calls: 1, embedding_cache_hits: 0 });
    expect(line.degraded).toBe(false);
    expect(JSON.parse(JSON.stringify(line)).ranking[0].method).toBe("dense-legacy");
  });

  it("stamps fake_embeddings on every line when fake", () => {
    const line = buildRankingLine({
      caseId: "x", items: [], latencyMs: 1, embeddingCalls: 0, cacheHits: 0,
      variant: "threshold-none", threshold: -1, driver: "neon", fake: true,
    });
    expect(line.meta.fake_embeddings).toBe(true);
    expect(line.ranking).toEqual([]);
  });
});

describe("fake embeddings", () => {
  it("are deterministic, 3072-d unit vectors, whitespace-insensitive like sanitizeInput", () => {
    const a = fakeEmbedding(EMBEDDING_MODEL_ID, "hello   world");
    const b = fakeEmbedding(EMBEDDING_MODEL_ID, " hello world\n");
    const c = fakeEmbedding(EMBEDDING_MODEL_ID, "hello there");
    expect(a).toHaveLength(EMBEDDING_DIMENSIONS);
    expect(a).toEqual(b);
    expect(a).not.toEqual(c);
    expect(Math.sqrt(a.reduce((s, x) => s + x * x, 0))).toBeCloseTo(1, 6);
  });

  it("refuse the default baseline path and any dir without FAKE in its name", () => {
    expect(() => resolveOutDir("benchmarks/runs/baseline-1.0", true, ROOT)).toThrow(/FAKE/);
    expect(() => resolveOutDir("benchmarks/runs/other", true, ROOT)).toThrow(/FAKE/);
    expect(resolveOutDir("benchmarks/runs/baseline-1.0-FAKE", true, ROOT)).toBe(
      path.join(ROOT, "benchmarks/runs/baseline-1.0-FAKE")
    );
    expect(resolveOutDir("benchmarks/runs/baseline-1.0", false, ROOT)).toBe(
      path.join(ROOT, "benchmarks/runs/baseline-1.0")
    );
  });

  it("run() refuses to write fake results to the default path before touching anything", async () => {
    await expect(
      run(parseArgs(["--database-url", "postgresql://x@localhost/y"]), { BASELINE_FAKE_EMBEDDINGS: "1", EVAL_ALLOW_WRITE: "1" }, () => undefined)
    ).rejects.toThrow(/FAKE/);
  });
});

describe("safety refusals", () => {
  const url = "postgresql://scribe:scribe@localhost:54329/baseline";
  it("requires an explicit database URL (no NEON_DATABASE_URL fallback)", () => {
    expect(() =>
      assertSafeConfig({ databaseUrl: undefined, env: { EVAL_ALLOW_WRITE: "1", NEON_DATABASE_URL: url }, protectedUrls: [] })
    ).toThrow(/no database URL/);
    expect(() => assertSafeConfig({ databaseUrl: "  ", env: { EVAL_ALLOW_WRITE: "1" }, protectedUrls: [] })).toThrow(/no database URL/);
  });
  it("requires EVAL_ALLOW_WRITE=1", () => {
    expect(() => assertSafeConfig({ databaseUrl: url, env: {}, protectedUrls: [] })).toThrow(/EVAL_ALLOW_WRITE/);
    expect(() => assertSafeConfig({ databaseUrl: url, env: { EVAL_ALLOW_WRITE: "true" }, protectedUrls: [] })).toThrow(/EVAL_ALLOW_WRITE/);
  });
  it("refuses the app's own NEON_DATABASE_URL", () => {
    expect(() =>
      assertSafeConfig({ databaseUrl: url, env: { EVAL_ALLOW_WRITE: "1" }, protectedUrls: [undefined, url] })
    ).toThrow(/NEON_DATABASE_URL/);
    expect(assertSafeConfig({ databaseUrl: url, env: { EVAL_ALLOW_WRITE: "1" }, protectedUrls: ["postgresql://other"] })).toBe(url);
  });
  it("run() refuses on missing URL or missing allow flag", async () => {
    await expect(run(parseArgs([]), { EVAL_ALLOW_WRITE: "1" }, () => undefined)).rejects.toThrow(/no database URL/);
    await expect(run(parseArgs(["--database-url", url]), {}, () => undefined)).rejects.toThrow(/EVAL_ALLOW_WRITE/);
  });
  it("refuses a database with personal workspace files unless explicitly allowed", () => {
    expect(() => assertNoPersonalData(3, {})).toThrow(/personal/);
    expect(() => assertNoPersonalData(0, {})).not.toThrow();
    expect(() => assertNoPersonalData(3, { EVAL_ALLOW_POPULATED_DB: "1" })).not.toThrow();
  });
  it("refuses any workspace that is not the dedicated eval workspace", () => {
    expect(() => assertEvalWorkspace({ id: "w", kind: "personal", ownerUserId: "user_1" })).toThrow(/dedicated eval workspace/);
    expect(() => assertEvalWorkspace({ id: "w", kind: "eval", ownerUserId: "someone-else" })).toThrow(/dedicated eval workspace/);
    expect(() => assertEvalWorkspace({ id: "w", kind: "eval", ownerUserId: EVAL_OWNER_ID })).not.toThrow();
  });
});

describe("corpus handling", () => {
  it("maps extensions to the intake mime types", () => {
    expect(sourceMimeTypeForName("a.pdf")).toBe("application/pdf");
    expect(sourceMimeTypeForName("a.TXT")).toBe("text/plain");
    expect(sourceMimeTypeForName("a.md")).toBe("text/markdown");
    expect(sourceMimeTypeForName("a.csv")).toBe("text/csv");
    expect(sourceMimeTypeForName("a.docx")).toBe(
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    );
    expect(sourceMimeTypeForName("a.png")).toBeNull();
  });

  it("excludes labels and ground truth from the ingested set", () => {
    expect(isCorpusDocument("FACTS.md")).toBe(false);
    expect(isCorpusDocument("facility-safety-notice-scanned.truth.txt")).toBe(false);
    expect(isCorpusDocument("vendor-sla.pdf")).toBe(true);
  });

  it("extract + chunk runs offline on all 24 corpus documents with the current TS code", async () => {
    const plans = await planCorpus(path.join(ROOT, "fixtures/corpus"));
    const summary = summarizePlans(plans);
    expect(plans).toHaveLength(24);
    const byName = Object.fromEntries(plans.map((p) => [p.name, p]));
    // Findings that hold on every Node version:
    expect(byName["facility-safety-notice-scanned.pdf"].status).toBe("failed");
    expect(byName["facility-safety-notice-scanned.pdf"].error).toMatch(/No text extracted/);
    expect(byName["office-locations.csv"].error).toMatch(/Invalid Record Length/);
    expect(byName["price-list-2026.csv"].error).toMatch(/Invalid Record Length/);
    // Every non-PDF document that parses yields at least one chunk, all within the 4000-char splitter.
    for (const p of plans.filter((x) => x.status === "ok")) {
      expect(p.chunks.length).toBeGreaterThan(0);
      for (const c of p.chunks) expect(c.content.length).toBeLessThanOrEqual(4000);
    }
    expect(summary.docs_total).toBe(24);
    expect(summary.docs_extracted + summary.docs_failed).toBe(24);
    expect(summary.chunks_total).toBe(plans.reduce((n, p) => n + p.chunks.length, 0));
  }, 60_000);
});

describe("embedding cache and fetch shim", () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "emb-cache-"));
  afterAll(() => fs.rmSync(tmp, { recursive: true, force: true }));

  it("cache key is stable and normalised", () => {
    const k = embeddingCacheKey("models/m", "a   b\n");
    expect(k).toBe(embeddingCacheKey("models/m", "a b"));
    expect(k).toMatch(/^[0-9a-f]{64}$/);
    expect(k).not.toBe(embeddingCacheKey("models/other", "a b"));
    // definition: sha256(model + sanitized text)
    expect(embeddingCacheKey("models/m", "a b")).toBe(
      execFileSync("node", ["-e", "process.stdout.write(require('crypto').createHash('sha256').update('models/m'+'a b').digest('hex'))"]).toString()
    );
  });

  function vec(seed: number) {
    const v = new Array<number>(EMBEDDING_DIMENSIONS).fill(0);
    v[seed % EMBEDDING_DIMENSIONS] = 1;
    return v;
  }
  const BATCH = "https://generativelanguage.googleapis.com/v1beta/models/m:batchEmbedContents";
  const SINGLE = "https://generativelanguage.googleapis.com/v1beta/models/m:embedContent";
  const batchBody = (texts: string[]) =>
    JSON.stringify({ requests: texts.map((text) => ({ model: "m", content: { parts: [{ text }] } })) });

  it("serves repeats from the on-disk cache and forwards only misses", async () => {
    const file = path.join(tmp, "c.jsonl");
    const sent: string[][] = [];
    const realFetch = (async (_url: unknown, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body));
      const texts = body.requests
        ? body.requests.map((r: any) => r.content.parts[0].text)
        : [body.content.parts[0].text];
      sent.push(texts);
      const values = texts.map((t: string) => vec(t.length));
      return new Response(JSON.stringify(body.requests ? { embeddings: values.map((v: number[]) => ({ values: v })) } : { embedding: { values: values[0] } }));
    }) as typeof fetch;
    const stats: EmbeddingStats = { apiCalls: 0, cacheHits: 0, cacheMisses: 0, fakeServed: 0 };
    const restore = installEmbeddingShim({ fake: false, cache: new EmbeddingCache(file), model: "models/m", stats, realFetch });
    try {
      const r1 = await (await fetch(BATCH, { method: "POST", body: batchBody(["aa", "bbb"]) })).json();
      expect(r1.embeddings).toHaveLength(2);
      expect(stats).toMatchObject({ apiCalls: 1, cacheHits: 0, cacheMisses: 2 });
      const r2 = await (await fetch(BATCH, { method: "POST", body: batchBody(["aa", "bbb", "cccc"]) })).json();
      expect(r2.embeddings).toHaveLength(3);
      expect(sent[1]).toEqual(["cccc"]);
      expect(stats).toMatchObject({ apiCalls: 2, cacheHits: 2 });
      const r3 = await (await fetch(SINGLE, { method: "POST", body: JSON.stringify({ model: "m", content: { parts: [{ text: "aa" }] } }) })).json();
      expect(r3.embedding.values).toEqual(r1.embeddings[0].values);
      expect(stats.apiCalls).toBe(2);
    } finally {
      restore();
    }
    // a fresh process-level cache reads the same JSONL back
    expect(new EmbeddingCache(file).size).toBe(3);
  });

  it("fake mode never calls the network and never writes the cache", async () => {
    const file = path.join(tmp, "fake.jsonl");
    const realFetch = vi.fn() as unknown as typeof fetch;
    const stats: EmbeddingStats = { apiCalls: 0, cacheHits: 0, cacheMisses: 0, fakeServed: 0 };
    const restore = installEmbeddingShim({ fake: true, cache: null, stats, realFetch });
    try {
      const res = await (await fetch(BATCH, { method: "POST", body: batchBody(["x", "y"]) })).json();
      expect(res.embeddings[0].values).toEqual(fakeEmbedding(EMBEDDING_MODEL_ID, "x"));
      expect(stats).toMatchObject({ apiCalls: 0, fakeServed: 2 });
      expect(realFetch).not.toHaveBeenCalled();
      expect(fs.existsSync(file)).toBe(false);
    } finally {
      restore();
    }
  });

  it("passes non-embedding requests through untouched", async () => {
    const realFetch = vi.fn(async () => new Response("ok")) as unknown as typeof fetch;
    const stats: EmbeddingStats = { apiCalls: 0, cacheHits: 0, cacheMisses: 0, fakeServed: 0 };
    const restore = installEmbeddingShim({ fake: true, cache: null, stats, realFetch });
    try {
      expect(await (await fetch("https://example.test/x")).text()).toBe("ok");
    } finally {
      restore();
    }
  });
});

describe("argument parsing", () => {
  it("applies defaults and rejects unknown flags", () => {
    const a = parseArgs([]);
    expect(a).toMatchObject({ corpus: "fixtures/corpus", dataset: "benchmarks/datasets/golden-v1", out: "benchmarks/runs/baseline-1.0", driver: "neon", reset: false });
    expect(parseArgs(["--driver", "psql", "--reset", "--limit", "5"])).toMatchObject({ driver: "psql", reset: true, limit: 5 });
    expect(() => parseArgs(["--driver", "mysql"])).toThrow();
    expect(() => parseArgs(["--nope"])).toThrow(/unknown argument/);
  });
});

// ---------------------------------------------------------------------------
// Integration: whole script, fake embeddings, local docker pgvector Postgres.
//   docker exec scribe-db-1 psql -U scribe -d scribe -c 'CREATE DATABASE baseline'   (the test creates it if missing)
//   TEST_DATABASE_URL=postgresql://scribe:scribe@localhost:54329/baseline npx vitest run __tests__/eval-baseline.test.ts
// Optional: BASELINE_NODE_DIR=/usr/local/bin (a Node on which pdf-parse works) so all text PDFs ingest in the script.
// ---------------------------------------------------------------------------
const testUrl = process.env.TEST_DATABASE_URL;
describe.skipIf(!testUrl)("integration: fake-embedding run against local Postgres", () => {
  const dbName = testUrl ? new URL(testUrl).pathname.slice(1) : "";
  const psql = (url: string, sql: string) =>
    execFileSync("psql", [url, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], { encoding: "utf8" }).trim();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "baseline-it-"));
  afterAll(() => fs.rmSync(tmp, { recursive: true, force: true }));

  const runScript = (extra: string[], envExtra: Record<string, string>) =>
    spawnSync("npx", ["tsx", "scripts/eval-baseline.ts", "--driver", "psql", "--database-url", testUrl!, ...extra], {
      cwd: ROOT,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
      env: {
        ...process.env,
        // BASELINE_NODE_DIR: directory of a Node on which pdf-parse works (e.g. /usr/local/bin for Node 18.16)
        ...(process.env.BASELINE_NODE_DIR ? { PATH: `${process.env.BASELINE_NODE_DIR}:${process.env.PATH}` } : {}),
        EVAL_ALLOW_WRITE: "1",
        BASELINE_FAKE_EMBEDDINGS: "1",
        ...envExtra,
      },
    });

  it("ingests into the eval workspace only, writes rankings, and scribe-eval scores them", () => {
    expect(dbName).toMatch(/^baseline/); // apply-migrations.sh drops the public schema: dedicated DB only
    const admin = new URL(testUrl!);
    admin.pathname = "/postgres";
    if (psql(admin.href, `SELECT 1 FROM pg_database WHERE datname='${dbName}'`) !== "1") {
      psql(admin.href, `CREATE DATABASE ${dbName}`);
    }
    execFileSync("bash", ["scripts/apply-migrations.sh", testUrl!], { cwd: ROOT, stdio: "pipe" });

    // decoy "production-like" data that must never be touched
    psql(testUrl!, `INSERT INTO workspaces (id, kind, owner_user_id) VALUES ('99999999-0000-0000-0000-000000000001','personal','user_decoy')`);
    psql(testUrl!, `INSERT INTO files (id, name, type, size, user_id, workspace_id, status) VALUES (900001,'decoy.txt','text/plain',1,'user_decoy','99999999-0000-0000-0000-000000000001','ready')`);
    psql(testUrl!, `INSERT INTO documents (file_id, chunk_index, content, embeddings) VALUES (900001,0,'decoy',array_fill(0.0::real, ARRAY[3072])::vector)`);

    const out = path.join(tmp, "baseline-FAKE");
    // 1. a database holding personal files is refused by default, before anything is written
    const refused = runScript(["--out", out], {});
    expect(refused.status).not.toBe(0);
    expect(refused.stderr).toMatch(/personal/);
    expect(psql(testUrl!, `SELECT count(*) FROM workspaces WHERE kind='eval'`)).toBe("0");

    // 2. explicit override: runs, scoped to the eval workspace
    const ok = runScript(["--out", out, "--reset"], { EVAL_ALLOW_POPULATED_DB: "1" });
    expect(ok.stderr + ok.stdout).toContain("wrote");
    expect(ok.status, ok.stderr + ok.stdout).toBe(0);

    const evalWs = psql(testUrl!, `SELECT id FROM workspaces WHERE kind='eval' AND owner_user_id='eval-golden-v1'`);
    expect(evalWs).toMatch(/^[0-9a-f-]{36}$/);
    expect(Number(psql(testUrl!, `SELECT count(*) FROM files WHERE workspace_id='${evalWs}'`))).toBeGreaterThan(0);
    // only the decoy lives anywhere else, and it is intact
    expect(psql(testUrl!, `SELECT string_agg(name, ',') FROM files WHERE workspace_id <> '${evalWs}'`)).toBe("decoy.txt");
    expect(psql(testUrl!, `SELECT count(*) FROM documents WHERE file_id = 900001`)).toBe("1");
    expect(psql(testUrl!, `SELECT count(*) FROM documents d JOIN files f ON f.id=d.file_id WHERE f.workspace_id='${evalWs}' AND f.user_id <> 'eval-golden-v1'`)).toBe("0");
    expect(psql(testUrl!, `SELECT count(*) FROM files WHERE workspace_id='${evalWs}' AND status='failed'`)).not.toBe("");

    // 3. second run without --reset refuses
    const again = runScript(["--out", out], { EVAL_ALLOW_POPULATED_DB: "1" });
    expect(again.status).not.toBe(0);
    expect(again.stderr).toMatch(/--reset/);

    // 4. rankings: one line per case, fake-stamped
    const caseCount = fs.readFileSync(path.join(ROOT, "benchmarks/datasets/golden-v1/cases.jsonl"), "utf8").split("\n").filter(Boolean).length;
    for (const variant of ["threshold-0.3", "threshold-none"]) {
      const lines = fs.readFileSync(path.join(out, `rankings.${variant}.jsonl`), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l));
      expect(lines).toHaveLength(caseCount);
      expect(new Set(lines.map((l) => l.case_id)).size).toBe(caseCount);
      for (const l of lines) {
        expect(l.meta.fake_embeddings).toBe(true);
        expect(l.ranking.length).toBeLessThanOrEqual(10);
        for (const item of l.ranking) expect(item.method).toBe("dense-legacy");
      }
      if (variant === "threshold-none") expect(lines.some((l) => l.ranking.length > 0)).toBe(true);
    }
    const meta = JSON.parse(fs.readFileSync(path.join(out, "baseline-meta.json"), "utf8"));
    expect(meta.fake_embeddings).toBe(true);
    expect(meta.embedding.dimensions).toBe(3072);
    expect(meta.chunker.chunk_size).toBe(4000);
    expect(meta.chunker.chunk_overlap).toBe(400);

    // 5. the Python scorer accepts the files (plumbing proof only; the numbers are meaningless)
    const scoreOut = path.join(tmp, "score");
    for (const variant of ["threshold-0.3", "threshold-none"]) {
      const scored = spawnSync(
        "uv",
        [
          "run", "scribe-eval", "score",
          "--rankings", path.join(out, `rankings.${variant}.jsonl`),
          "--dataset", path.join(ROOT, "benchmarks/datasets/golden-v1"),
          "--split", "dev",
          "--profile", "FAKE-plumbing-only",
          "--out", scoreOut,
        ],
        { cwd: path.join(ROOT, "intelligence"), encoding: "utf8" }
      );
      expect(scored.status, scored.stderr).toBe(0);
      expect(scored.stdout).toMatch(/run written/);
    }
  }, 600_000);
});
