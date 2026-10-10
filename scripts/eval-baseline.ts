/**
 * Scribe 2.0 T07: frozen retrieval baseline for Scribe 1.0.
 *
 *   npx tsx scripts/eval-baseline.ts [--corpus fixtures/corpus] [--dataset benchmarks/datasets/golden-v1]
 *       [--out benchmarks/runs/baseline-1.0] [--driver neon|psql] [--database-url URL] [--reset] [--limit N]
 *       [--extract-only]
 *
 * It measures the CURRENT TypeScript pipeline as it is (nothing in lib/ is changed or re-implemented):
 *   - lib/ingestion/extract.ts extractTextFromBuffer -> lib/chunking.ts chunkContent
 *     -> lib/embeddings.ts generateEmbeddings -> files/documents rows (same shape as lib/ingestion/source-intake.ts)
 *   - retrieval: lib/search.ts searchDocuments (driver neon) or the SAME SQL built by drizzle and run through
 *     the psql CLI (driver psql). A unit test proves the two SQL texts are identical.
 *
 * Safety (the live run writes to a database):
 *   - the database URL must be given explicitly (--database-url or EVAL_DATABASE_URL). NEON_DATABASE_URL is
 *     never used, and the URL is refused if it equals NEON_DATABASE_URL (shell or .env.local);
 *   - EVAL_ALLOW_WRITE=1 is required;
 *   - the DB is refused if any `personal` workspace has files (a production-like database), unless
 *     EVAL_ALLOW_POPULATED_DB=1 (e.g. a Neon branch of production: everything here is scoped to the eval workspace);
 *   - every write/delete is scoped to ONE workspace: kind 'eval', owner 'eval-golden-v1'.
 *
 * The embedding layer is intercepted at the fetch boundary (lib/embeddings.ts is untouched): a JSONL cache
 * (benchmarks/cache/ts-embeddings.jsonl, key sha256(model + sanitized text)) serves repeated texts, and
 * BASELINE_FAKE_EMBEDDINGS=1 serves deterministic fake vectors instead of calling the API (plumbing only).
 */
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { and, cosineDistance, desc, eq, gt, inArray, sql } from "drizzle-orm";
import { drizzle } from "drizzle-orm/neon-http";
import { parse as parseDotenv } from "dotenv";
import { textsplitter } from "../lib/chunking";
import { EMBEDDING_MODEL_ID, EMBEDDING_MODEL_LABEL } from "../lib/embedding-config";
import { sourceMimeTypeForName } from "../lib/ingestion/source-intake-policy";
import * as schema from "../lib/db-schema";
import { documents, files, workspaces } from "../lib/db-schema";

export const EVAL_OWNER_ID = "eval-golden-v1";
export const EVAL_WORKSPACE_KIND = "eval";
export const EMBEDDING_DIMENSIONS = 3072;
export const DEFAULT_OUT = "benchmarks/runs/baseline-1.0";
export const DEFAULT_CACHE = "benchmarks/cache/ts-embeddings.jsonl";
export const RANKING_LIMIT = 10;
export const VARIANTS = [
  { name: "threshold-0.3", threshold: 0.3 },
  { name: "threshold-none", threshold: -1 },
] as const;
/** Files in the corpus directory that are not documents (labels / ground truth). */
export function isCorpusDocument(name: string): boolean {
  return name !== "FACTS.md" && !name.endsWith(".truth.txt") && !name.startsWith(".");
}

// ------------------------------------------------------------------ safety

export interface SafetyInput {
  databaseUrl: string | undefined;
  env: Record<string, string | undefined>;
  /** URLs that must never be a target (the app's own NEON_DATABASE_URL, from shell and .env.local). */
  protectedUrls: Array<string | undefined>;
}

export function assertSafeConfig(input: SafetyInput): string {
  const url = input.databaseUrl?.trim();
  if (!url) {
    throw new Error(
      "Refusing to run: no database URL. Pass --database-url or set EVAL_DATABASE_URL explicitly " +
        "(NEON_DATABASE_URL is never used as a fallback)."
    );
  }
  if (input.env.EVAL_ALLOW_WRITE !== "1") {
    throw new Error("Refusing to run: set EVAL_ALLOW_WRITE=1 to allow this script to write to the target database.");
  }
  if (input.protectedUrls.some((p) => p && p.trim() === url)) {
    throw new Error("Refusing to run: the target database URL equals NEON_DATABASE_URL (the app database).");
  }
  return url;
}

export function assertNoPersonalData(personalFileCount: number, env: Record<string, string | undefined>) {
  if (personalFileCount > 0 && env.EVAL_ALLOW_POPULATED_DB !== "1") {
    throw new Error(
      `Refusing to run: the database has ${personalFileCount} file(s) in 'personal' workspaces (looks like a real ` +
        "app database). Use an empty database, or a branch you are sure about and set EVAL_ALLOW_POPULATED_DB=1 " +
        "(all writes stay inside the eval workspace)."
    );
  }
}

export function assertEvalWorkspace(ws: { id: string; kind: string; ownerUserId: string | null }) {
  if (ws.kind !== EVAL_WORKSPACE_KIND || ws.ownerUserId !== EVAL_OWNER_ID) {
    throw new Error(
      `Refusing to run: workspace ${ws.id} is kind '${ws.kind}' owner '${ws.ownerUserId}', ` +
        `not the dedicated eval workspace (kind 'eval', owner '${EVAL_OWNER_ID}').`
    );
  }
}

export function resolveOutDir(out: string, fake: boolean, cwd = process.cwd()): string {
  const abs = path.resolve(cwd, out);
  if (fake) {
    const defaultAbs = path.resolve(cwd, DEFAULT_OUT);
    if (abs === defaultAbs || !/FAKE/.test(path.basename(abs))) {
      throw new Error(
        "BASELINE_FAKE_EMBEDDINGS=1: refusing to write fake results to the baseline path. " +
          "Use an --out directory whose name contains FAKE (e.g. benchmarks/runs/baseline-1.0-FAKE)."
      );
    }
  }
  return abs;
}

// ------------------------------------------------------- embeddings / cache

export const sanitizeInput = (text: string) => text.replace(/\s+/g, " ").trim();

/** Same normalisation as lib/embeddings.ts sanitizeInput (it is not exported there). */
export function embeddingCacheKey(model: string, text: string): string {
  return createHash("sha256").update(model + sanitizeInput(text)).digest("hex");
}

/** Deterministic fake unit vector derived from sha256(model + sanitized text). Plumbing tests only. */
export function fakeEmbedding(model: string, text: string, dims = EMBEDDING_DIMENSIONS): number[] {
  const seed = createHash("sha256").update("FAKE" + model + sanitizeInput(text)).digest();
  let s = seed.readUInt32LE(0) || 1;
  const v = new Array<number>(dims);
  let norm = 0;
  for (let i = 0; i < dims; i++) {
    // xorshift32
    s ^= s << 13;
    s >>>= 0;
    s ^= s >>> 17;
    s ^= s << 5;
    s >>>= 0;
    const x = s / 0xffffffff - 0.5;
    v[i] = x;
    norm += x * x;
  }
  const inv = 1 / Math.sqrt(norm);
  for (let i = 0; i < dims; i++) v[i] *= inv;
  return v;
}

export interface EmbeddingStats {
  /** Real network requests to the embedding API (one per embedContent / batchEmbedContents call actually sent). */
  apiCalls: number;
  cacheHits: number;
  cacheMisses: number;
  fakeServed: number;
}

export class EmbeddingCache {
  private map = new Map<string, number[]>();
  constructor(private readonly file: string) {
    if (fs.existsSync(file)) {
      for (const line of fs.readFileSync(file, "utf8").split("\n")) {
        if (!line.trim()) continue;
        const row = JSON.parse(line) as { k: string; v: number[] };
        this.map.set(row.k, row.v);
      }
    }
  }
  get(key: string) {
    return this.map.get(key);
  }
  set(key: string, model: string, values: number[]) {
    if (this.map.has(key)) return;
    this.map.set(key, values);
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    fs.appendFileSync(this.file, JSON.stringify({ k: key, model, dims: values.length, v: values }) + "\n");
  }
  get size() {
    return this.map.size;
  }
}

type FetchFn = typeof fetch;

/**
 * Wrap global fetch so lib/embeddings.ts (unchanged) is served from the cache / fake generator.
 * Only embedContent and batchEmbedContents URLs are touched. Returns a restore function.
 */
export function installEmbeddingShim(opts: {
  fake: boolean;
  cache: EmbeddingCache | null;
  model?: string;
  stats: EmbeddingStats;
  realFetch?: FetchFn;
}): () => void {
  const model = opts.model ?? EMBEDDING_MODEL_ID;
  const realFetch = opts.realFetch ?? globalThis.fetch;
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

  const shim: FetchFn = async (input, init) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const isBatch = url.includes(":batchEmbedContents");
    const isSingle = !isBatch && url.includes(":embedContent");
    if (!isBatch && !isSingle) return realFetch(input, init);

    const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
    const texts: string[] = isBatch
      ? (body.requests as { content: { parts: { text: string }[] } }[]).map((r) => r.content.parts[0].text)
      : [(body.content as { parts: { text: string }[] }).parts[0].text];

    const out: (number[] | null)[] = texts.map(() => null);
    const missing: number[] = [];
    texts.forEach((t, i) => {
      if (opts.fake) {
        out[i] = fakeEmbedding(model, t);
        opts.stats.fakeServed += 1;
        return;
      }
      const hit = opts.cache?.get(embeddingCacheKey(model, t));
      if (hit) {
        out[i] = hit;
        opts.stats.cacheHits += 1;
      } else {
        missing.push(i);
        opts.stats.cacheMisses += 1;
      }
    });

    if (missing.length > 0) {
      const forwardBody = isBatch
        ? { ...body, requests: missing.map((i) => (body.requests as unknown[])[i]) }
        : body;
      opts.stats.apiCalls += 1;
      const res = await realFetch(input, { ...init, body: JSON.stringify(forwardBody) });
      if (!res.ok) return res; // lib/embeddings.ts handles 429 / errors exactly as in production
      const data = (await res.json()) as {
        embeddings?: { values: number[] }[];
        embedding?: { values: number[] };
      };
      const vectors = isBatch ? (data.embeddings ?? []).map((e) => e.values) : [data.embedding!.values];
      if (vectors.length !== missing.length) {
        throw new Error(`embedding API returned ${vectors.length} vectors for ${missing.length} inputs`);
      }
      missing.forEach((idx, j) => {
        if (vectors[j].length !== EMBEDDING_DIMENSIONS) {
          throw new Error(`embedding has ${vectors[j].length} dims, expected ${EMBEDDING_DIMENSIONS}`);
        }
        out[idx] = vectors[j];
        opts.cache?.set(embeddingCacheKey(model, texts[idx]), model, vectors[j]);
      });
    }
    return json(isBatch ? { embeddings: out.map((values) => ({ values })) } : { embedding: { values: out[0] } });
  };

  const original = globalThis.fetch;
  globalThis.fetch = shim;
  return () => {
    globalThis.fetch = original;
  };
}

// ----------------------------------------------------------- SQL / executors

// A connection-less drizzle instance of the same dialect lib/db-config.ts uses (neon-http). Query builders
// from it produce exactly the SQL the real `db` would send.
const queryDb = drizzle.mock({ schema });

export interface SqlExecutor {
  /** Run a statement that returns rows; each row is an array in select-list order. ncols = select-list width. */
  rows(query: { sql: string; params: unknown[] }, ncols: number): Promise<unknown[][]>;
  /** Run a statement without a result set. */
  exec(query: { sql: string; params: unknown[] }): Promise<void>;
}

export function sqlLiteral(value: unknown): string {
  if (value === null || value === undefined) return "NULL";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("non-finite number in SQL parameter");
    return String(value);
  }
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  if (value instanceof Date) return `'${value.toISOString()}'`;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text.includes("\0")) throw new Error("NUL byte in SQL parameter");
  return `'${text.replace(/'/g, "''")}'`;
}

/** Substitute $1..$n with literals (psql cannot bind parameters). Drizzle SQL contains no $ inside strings. */
export function inlineParams(text: string, params: unknown[]): string {
  return text.replace(/\$(\d+)/g, (_m, n: string) => {
    const idx = Number(n) - 1;
    if (idx < 0 || idx >= params.length) throw new Error(`SQL references $${n} but only ${params.length} params`);
    return sqlLiteral(params[idx]);
  });
}

function positionalWrapper(inner: string, ncols: number) {
  const cols = Array.from({ length: ncols }, (_, i) => `c${i}`).join(",");
  return `WITH q AS (${inner}) SELECT row_to_json(t) FROM q AS t(${cols})`;
}

export function psqlExecutor(url: string): SqlExecutor {
  const run = (input: string) => {
    const res = spawnSync("psql", [url, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", "-f", "-"], {
      input,
      encoding: "utf8",
      maxBuffer: 512 * 1024 * 1024,
    });
    if (res.status !== 0) throw new Error(`psql failed: ${(res.stderr || String(res.error)).slice(0, 2000)}`);
    return res.stdout;
  };
  return {
    async rows(q, ncols) {
      const stdout = run(positionalWrapper(inlineParams(q.sql, q.params), ncols) + ";\n");
      return stdout
        .split("\n")
        .filter((l) => l.length > 0)
        .map((l) => {
          const obj = JSON.parse(l) as Record<string, unknown>;
          return Array.from({ length: ncols }, (_, i) => obj[`c${i}`]);
        });
    },
    async exec(q) {
      run(inlineParams(q.sql, q.params) + ";\n");
    },
  };
}

export async function neonExecutor(url: string): Promise<SqlExecutor> {
  const { neon } = await import("@neondatabase/serverless");
  const client = neon(url);
  return {
    async rows(q) {
      return (await client.query(q.sql, q.params, { arrayMode: true })) as unknown[][];
    },
    async exec(q) {
      await client.query(q.sql, q.params);
    },
  };
}

/**
 * The query lib/search.ts searchDocuments builds, replicated with the same drizzle expressions. A unit test
 * records the query search.ts really builds (mocked db) and asserts `toSQL()` equality with this one.
 */
export function buildSearchQuery(
  embedding: number[],
  userId: string,
  workspaceId: string,
  limit: number,
  threshold: number
) {
  const similarity = sql<number>`1 - (${cosineDistance(documents.embeddings, embedding)})`;
  return queryDb
    .select({
      id: documents.id,
      content: documents.content,
      metadata: documents.metadata,
      file: {
        id: files.id,
        name: files.name,
        type: files.type,
      },
      similarity,
    })
    .from(documents)
    .innerJoin(files, eq(documents.fileId, files.id))
    .where(and(gt(similarity, threshold), eq(files.userId, userId), eq(files.workspaceId, workspaceId)))
    .orderBy(desc(similarity))
    .limit(limit);
}
export const SEARCH_COLUMNS = 7;

export interface RankedItem {
  chunk_id: string;
  doc: string;
  text: string;
  score: number;
  method: "dense-legacy";
}

export function rowToRankedItem(row: unknown[]): RankedItem {
  // select list order: documents.id, content, metadata, files.id, files.name, files.type, similarity
  return {
    chunk_id: String(row[0]),
    doc: String(row[4]),
    text: String(row[1]),
    score: Number(row[6]),
    method: "dense-legacy",
  };
}

export interface RankingLine {
  case_id: string;
  ranking: RankedItem[];
  latency_ms: number;
  cost: { embedding_calls: number; embedding_cache_hits: number };
  degraded: boolean;
  meta: {
    variant: string;
    threshold: number;
    limit: number;
    driver: string;
    fake_embeddings: boolean;
  };
}

export function buildRankingLine(args: {
  caseId: string;
  items: RankedItem[];
  latencyMs: number;
  embeddingCalls: number;
  cacheHits: number;
  variant: string;
  threshold: number;
  driver: string;
  fake: boolean;
}): RankingLine {
  return {
    case_id: args.caseId,
    ranking: args.items,
    latency_ms: Math.round(args.latencyMs * 10) / 10,
    cost: { embedding_calls: args.embeddingCalls, embedding_cache_hits: args.cacheHits },
    degraded: false,
    meta: {
      variant: args.variant,
      threshold: args.threshold,
      limit: RANKING_LIMIT,
      driver: args.driver,
      fake_embeddings: args.fake,
    },
  };
}

// ----------------------------------------------------------- workspace / DB

const sel = (q: { toSQL(): { sql: string; params: unknown[] } }) => q.toSQL();

export async function ensureEvalWorkspace(exec: SqlExecutor): Promise<string> {
  const found = await exec.rows(
    sel(
      queryDb
        .select({ id: workspaces.id, kind: workspaces.kind, owner: workspaces.ownerUserId })
        .from(workspaces)
        .where(and(eq(workspaces.kind, EVAL_WORKSPACE_KIND), eq(workspaces.ownerUserId, EVAL_OWNER_ID)))
        .limit(1)
    ),
    3
  );
  if (found.length > 0) {
    const [id, kind, owner] = found[0] as [string, string, string | null];
    assertEvalWorkspace({ id, kind, ownerUserId: owner });
    return id;
  }
  const created = await exec.rows(
    sel(
      queryDb
        .insert(workspaces)
        .values({ kind: EVAL_WORKSPACE_KIND, ownerUserId: EVAL_OWNER_ID })
        .returning({ id: workspaces.id, kind: workspaces.kind, owner: workspaces.ownerUserId })
    ),
    3
  );
  const [id, kind, owner] = created[0] as [string, string, string | null];
  assertEvalWorkspace({ id, kind, ownerUserId: owner });
  return id;
}

export async function personalFileCount(exec: SqlExecutor): Promise<number> {
  const rows = await exec.rows(
    sel(
      queryDb
        .select({ n: sql<number>`count(*)` })
        .from(files)
        .innerJoin(workspaces, eq(files.workspaceId, workspaces.id))
        .where(eq(workspaces.kind, "personal"))
    ),
    1
  );
  return Number(rows[0][0]);
}

export async function evalFileCount(exec: SqlExecutor, workspaceId: string): Promise<number> {
  const rows = await exec.rows(
    sel(queryDb.select({ n: sql<number>`count(*)` }).from(files).where(eq(files.workspaceId, workspaceId))),
    1
  );
  return Number(rows[0][0]);
}

/** Deletes ONLY documents/files of the given workspace (caller passes the verified eval workspace id). */
export async function resetEvalWorkspace(exec: SqlExecutor, workspaceId: string) {
  const ids = queryDb.select({ id: files.id }).from(files).where(eq(files.workspaceId, workspaceId));
  await exec.exec(sel(queryDb.delete(documents).where(inArray(documents.fileId, ids))));
  await exec.exec(sel(queryDb.delete(files).where(eq(files.workspaceId, workspaceId))));
}

// ------------------------------------------------------------ corpus stage

export interface DocPlan {
  name: string;
  mime: string | null;
  bytes: number;
  status: "ok" | "failed";
  error?: string;
  chars?: number;
  pages?: number;
  extractedText?: string;
  chunks: { content: string; metadata: Record<string, unknown> }[];
}

/**
 * fs.readFileSync returns a slice of a shared 64 KiB pool on recent Node versions (byteOffset != 0).
 * pdf-parse's bundled pdf.js reads `buffer.buffer` ignoring the offset and then fails nondeterministically
 * ("bad XRef entry", "Illegal character"). Production hands extractTextFromBuffer a Buffer that owns its
 * ArrayBuffer (blob download), so give it the same here.
 */
export function readExactBuffer(file: string): Buffer {
  const src = fs.readFileSync(file);
  const buf = Buffer.from(new ArrayBuffer(src.length));
  src.copy(buf);
  return buf;
}

/** Offline stage: extract + chunk every corpus document with the current TS code. */
export async function planCorpus(corpusDir: string): Promise<DocPlan[]> {
  const { extractTextFromBuffer } = await import("../lib/ingestion/extract");
  const { chunkContent } = await import("../lib/chunking");
  const names = fs.readdirSync(corpusDir).filter(isCorpusDocument).sort();
  const plans: DocPlan[] = [];
  for (const name of names) {
    const buffer = readExactBuffer(path.join(corpusDir, name));
    const mime = sourceMimeTypeForName(name);
    const plan: DocPlan = { name, mime, bytes: buffer.length, status: "ok", chunks: [] };
    try {
      if (!mime) throw new Error(`Unsupported file type: ${name}`);
      const extraction = await extractTextFromBuffer({ buffer, fileName: name, mimeType: mime });
      const chunks = await chunkContent(extraction.extractedText, { fileName: name, numPages: extraction.numPages });
      plan.chars = extraction.extractedText.length;
      plan.pages = extraction.numPages;
      plan.extractedText = extraction.extractedText;
      plan.chunks = chunks as DocPlan["chunks"];
    } catch (err) {
      plan.status = "failed";
      plan.error = err instanceof Error ? err.message : String(err);
    }
    plans.push(plan);
  }
  return plans;
}

/**
 * pdf-parse bundles pdf.js 1.10, which breaks across sequential parses on some Node versions (observed on Node 26:
 * "bad XRef entry", "Illegal character: 41", "invalid top-level pages dictionary" on valid PDFs; Node 18.16 parses all
 * of them). Such failures measure the Node version, not Scribe, so a live run refuses to proceed on them.
 */
export const PDF_PARSER_STATE_ERRORS = /bad XRef entry|Illegal character|invalid top-level pages dictionary/;
export function pdfParserStateFailures(plans: DocPlan[]): DocPlan[] {
  return plans.filter((p) => p.status === "failed" && p.mime === "application/pdf" && PDF_PARSER_STATE_ERRORS.test(p.error ?? ""));
}

export function summarizePlans(plans: DocPlan[]) {
  const totalChunks = plans.reduce((n, p) => n + p.chunks.length, 0);
  const batches = plans.reduce((n, p) => n + Math.ceil(p.chunks.length / 100), 0);
  return {
    docs_total: plans.length,
    docs_extracted: plans.filter((p) => p.status === "ok").length,
    docs_failed: plans.filter((p) => p.status === "failed").length,
    chunks_total: totalChunks,
    embedding_batch_requests_estimate: batches,
    per_doc: plans.map((p) => ({
      name: p.name,
      mime: p.mime,
      status: p.status,
      chars: p.chars ?? null,
      pages: p.pages ?? null,
      chunks: p.chunks.length,
      error: p.error ?? null,
    })),
    failures: plans.filter((p) => p.status === "failed").map((p) => ({ name: p.name, error: p.error })),
    pdf_parser_state_failures: pdfParserStateFailures(plans).map((p) => p.name),
    scanned_pdf_failure: plans
      .filter((p) => p.status === "failed" && /scanned/i.test(p.name))
      .map((p) => ({ name: p.name, error: p.error })),
  };
}

async function ingestPlans(
  exec: SqlExecutor,
  workspaceId: string,
  plans: DocPlan[],
  embed: (texts: string[]) => Promise<number[][]>,
  log: (l: string) => void
) {
  for (const plan of plans) {
    const baseValues = {
      workspaceId,
      name: plan.name,
      size: plan.bytes,
      type: plan.mime ?? "application/octet-stream",
      userId: EVAL_OWNER_ID,
    };
    if (plan.status === "failed") {
      await exec.exec(
        sel(
          queryDb.insert(files).values({
            ...baseValues,
            status: "failed",
            processingError: plan.error,
            updatedAt: new Date(),
          })
        )
      );
      log(`  ${plan.name}: FAILED to extract (${plan.error}); recorded as status=failed`);
      continue;
    }
    const embeddings = await embed(plan.chunks.map((c) => c.content));
    if (embeddings.length !== plan.chunks.length || embeddings.some((e) => e.length !== EMBEDDING_DIMENSIONS)) {
      throw new Error(`${plan.name}: embedding count/dimension mismatch`);
    }
    const [row] = await exec.rows(
      sel(
        queryDb
          .insert(files)
          .values({
            ...baseValues,
            extractedText: plan.extractedText,
            textBytes: plan.extractedText!.length,
            status: "ready",
            processingError: null,
            updatedAt: new Date(),
          })
          .returning({ id: files.id })
      ),
      1
    );
    const fileId = Number(row[0]);
    const INSERT_BATCH = 20;
    for (let i = 0; i < plan.chunks.length; i += INSERT_BATCH) {
      const slice = plan.chunks.slice(i, i + INSERT_BATCH);
      await exec.exec(
        sel(
          queryDb.insert(documents).values(
            slice.map((chunk, j) => ({
              fileId,
              chunkIndex: chunk.metadata.chunkIndex as number,
              content: chunk.content,
              metadata: chunk.metadata,
              embeddings: embeddings[i + j],
            }))
          )
        )
      );
    }
    log(`  ${plan.name}: ${plan.chunks.length} chunks inserted (file id ${fileId})`);
  }
}

// -------------------------------------------------------------------- CLI

export interface Args {
  corpus: string;
  dataset: string;
  out: string;
  driver: "neon" | "psql";
  databaseUrl?: string;
  reset: boolean;
  limit?: number;
  extractOnly: boolean;
}

export function parseArgs(argv: string[]): Args {
  const a: Args = {
    corpus: "fixtures/corpus",
    dataset: "benchmarks/datasets/golden-v1",
    out: DEFAULT_OUT,
    driver: "neon",
    reset: false,
    extractOnly: false,
  };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const next = () => {
      const v = argv[++i];
      if (v === undefined) throw new Error(`${k} needs a value`);
      return v;
    };
    if (k === "--corpus") a.corpus = next();
    else if (k === "--dataset") a.dataset = next();
    else if (k === "--out") a.out = next();
    else if (k === "--driver") {
      const d = next();
      if (d !== "neon" && d !== "psql") throw new Error("--driver must be neon or psql");
      a.driver = d;
    } else if (k === "--database-url") a.databaseUrl = next();
    else if (k === "--reset") a.reset = true;
    else if (k === "--extract-only") a.extractOnly = true;
    else if (k === "--limit") {
      a.limit = Number(next());
      if (!Number.isInteger(a.limit) || a.limit < 1) throw new Error("--limit must be a positive integer");
    } else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

export interface GoldenCase {
  id: string;
  split: string;
  question: string;
}

export function loadCases(datasetDir: string, limit?: number): GoldenCase[] {
  const lines = fs
    .readFileSync(path.join(datasetDir, "cases.jsonl"), "utf8")
    .split("\n")
    .filter((l) => l.trim());
  const cases = lines.map((l) => {
    const c = JSON.parse(l) as GoldenCase;
    return { id: c.id, split: c.split, question: c.question };
  });
  return limit ? cases.slice(0, limit) : cases;
}

function gitInfo() {
  const git = (args: string[]) => spawnSync("git", args, { encoding: "utf8" });
  const head = git(["rev-parse", "HEAD"]);
  return {
    git_commit: head.status === 0 ? head.stdout.trim() : null,
    git_dirty_paths_in_ts_pipeline: git(["status", "--porcelain", "--", "lib", "drizzle", "package.json"])
      .stdout.split("\n")
      .filter(Boolean),
  };
}

/** Read ONLY specific keys from .env.local into an isolated object (never into process.env wholesale). */
function readEnvLocal(keys: string[]): Record<string, string | undefined> {
  const file = path.resolve(".env.local");
  if (!fs.existsSync(file)) return {};
  const parsed = parseDotenv(fs.readFileSync(file));
  return Object.fromEntries(keys.map((k) => [k, parsed[k]]));
}

export interface RunResult {
  outDir: string;
  meta: Record<string, unknown>;
}

export async function run(args: Args, env: Record<string, string | undefined>, log: (l: string) => void = console.log) {
  const fake = env.BASELINE_FAKE_EMBEDDINGS === "1";
  const corpusDir = path.resolve(args.corpus);
  const datasetDir = path.resolve(args.dataset);

  if (args.extractOnly) {
    const plans = await planCorpus(corpusDir);
    const summary = summarizePlans(plans);
    log(JSON.stringify(summary, null, 2));
    return { summary };
  }

  const outDir = resolveOutDir(args.out, fake);
  const local = readEnvLocal(["NEON_DATABASE_URL", "GOOGLE_GENERATIVE_AI_API_KEY"]);
  const url = assertSafeConfig({
    databaseUrl: args.databaseUrl ?? env.EVAL_DATABASE_URL,
    env,
    protectedUrls: [env.NEON_DATABASE_URL, local.NEON_DATABASE_URL],
  });
  if (!fake) {
    if (!env.GOOGLE_GENERATIVE_AI_API_KEY && local.GOOGLE_GENERATIVE_AI_API_KEY) {
      process.env.GOOGLE_GENERATIVE_AI_API_KEY = local.GOOGLE_GENERATIVE_AI_API_KEY;
    }
    if (!process.env.GOOGLE_GENERATIVE_AI_API_KEY) {
      throw new Error("GOOGLE_GENERATIVE_AI_API_KEY is not set (or use BASELINE_FAKE_EMBEDDINGS=1 for plumbing tests).");
    }
  }

  // lib/db-config.ts (imported by lib/search.ts) reads NEON_DATABASE_URL: point it at the eval target only.
  if (args.driver === "neon") process.env.NEON_DATABASE_URL = url;
  else delete process.env.NEON_DATABASE_URL;

  const cases = loadCases(datasetDir, args.limit);
  const exec = args.driver === "psql" ? psqlExecutor(url) : await neonExecutor(url);

  const stats: EmbeddingStats = { apiCalls: 0, cacheHits: 0, cacheMisses: 0, fakeServed: 0 };
  const cache = fake ? null : new EmbeddingCache(path.resolve(DEFAULT_CACHE));
  const restoreFetch = installEmbeddingShim({ fake, cache, stats });
  try {
    // Safety against the live DB, before any write.
    assertNoPersonalData(await personalFileCount(exec), env);
    const workspaceId = await ensureEvalWorkspace(exec);
    const existing = await evalFileCount(exec, workspaceId);
    if (existing > 0 && !args.reset) {
      throw new Error(
        `The eval workspace already has ${existing} file(s). Pass --reset to wipe and re-ingest it ` +
          "(embeddings come from the local cache, so this is free)."
      );
    }
    log("Stage 1/3: extract + chunk (current TS code)");
    const plans = await planCorpus(corpusDir);
    const summary = summarizePlans(plans);
    log(`  ${summary.docs_extracted}/${summary.docs_total} extracted, ${summary.chunks_total} chunks, failures: ${summary.docs_failed}`);
    if (summary.pdf_parser_state_failures.length > 0) {
      const msg =
        `pdf-parse failed on ${summary.pdf_parser_state_failures.join(", ")} with pdf.js internal-state errors on Node ${process.version}. ` +
        "This is a Node-version incompatibility, not a Scribe finding. Run with a Node version where all text PDFs parse (Node 18.16 does).";
      if (!fake) throw new Error(msg);
      log(`  WARNING (fake run, continuing): ${msg}`);
    }

    if (args.reset) {
      log(`Resetting eval workspace ${workspaceId} (only its files/documents)`);
      await resetEvalWorkspace(exec, workspaceId);
    }

    log("Stage 2/3: embed + insert");
    const { generateEmbeddings } = await import("../lib/embeddings");
    await ingestPlans(exec, workspaceId, plans, generateEmbeddings, log);
    const ingestStats = { ...stats };

    log("Stage 3/3: retrieval");
    const { generateEmbedding } = await import("../lib/embeddings");
    let searchDocuments: typeof import("../lib/search").searchDocuments | null = null;
    if (args.driver === "neon") searchDocuments = (await import("../lib/search")).searchDocuments;

    fs.mkdirSync(outDir, { recursive: true });
    const files_written: string[] = [];
    for (const variant of VARIANTS) {
      const file = path.join(outDir, `rankings.${variant.name}.jsonl`);
      const lines: string[] = [];
      for (const c of cases) {
        const callsBefore = stats.apiCalls;
        const hitsBefore = stats.cacheHits;
        const t0 = performance.now();
        let items: RankedItem[];
        if (searchDocuments) {
          const res = await searchDocuments(c.question, EVAL_OWNER_ID, workspaceId, RANKING_LIMIT, variant.threshold);
          items = res.documents.map((d) => ({
            chunk_id: String(d.id),
            doc: d.file.name,
            text: d.content,
            score: Number(d.similarity),
            method: "dense-legacy" as const,
          }));
        } else {
          const embedding = await generateEmbedding(c.question);
          const q = buildSearchQuery(embedding, EVAL_OWNER_ID, workspaceId, RANKING_LIMIT, variant.threshold).toSQL();
          const rows = await exec.rows(q, SEARCH_COLUMNS);
          items = rows.map(rowToRankedItem);
        }
        const latency = performance.now() - t0;
        lines.push(
          JSON.stringify(
            buildRankingLine({
              caseId: c.id,
              items,
              latencyMs: latency,
              embeddingCalls: stats.apiCalls - callsBefore,
              cacheHits: stats.cacheHits - hitsBefore,
              variant: variant.name,
              threshold: variant.threshold,
              driver: args.driver,
              fake,
            })
          )
        );
      }
      fs.writeFileSync(file, lines.join("\n") + "\n");
      files_written.push(path.basename(file));
      log(`  wrote ${file} (${lines.length} cases)`);
    }

    const datasetMeta = JSON.parse(fs.readFileSync(path.join(datasetDir, "meta.json"), "utf8")) as { version: string };
    const meta = {
      baseline: "scribe-1.0-frozen-retrieval",
      created_at: new Date().toISOString(),
      fake_embeddings: fake,
      dataset_version: datasetMeta.version,
      dataset_path: args.dataset,
      cases: cases.length,
      limited: args.limit ?? null,
      ...gitInfo(),
      embedding: { model_id: EMBEDDING_MODEL_ID, label: EMBEDDING_MODEL_LABEL, dimensions: EMBEDDING_DIMENSIONS },
      chunker: {
        splitter: "RecursiveCharacterTextSplitter",
        chunk_size: textsplitter.chunkSize,
        chunk_overlap: textsplitter.chunkOverlap,
        separators: ["\n\n", "\n", " ", ""],
      },
      retrieval: {
        function: "lib/search.ts searchDocuments",
        top_k: RANKING_LIMIT,
        scope: { user_id: EVAL_OWNER_ID, workspace_id: workspaceId },
        variants: VARIANTS.map((v) => ({ name: v.name, threshold: v.threshold, file: `rankings.${v.name}.jsonl` })),
      },
      driver: args.driver,
      ingestion: { ...summary, embedding_requests: ingestStats },
      query_embedding_requests: { api_calls: stats.apiCalls - ingestStats.apiCalls, cache_hits: stats.cacheHits - ingestStats.cacheHits },
      ranking_files: files_written,
      node_version: process.version,
    };
    fs.writeFileSync(path.join(outDir, "baseline-meta.json"), JSON.stringify(meta, null, 2) + "\n");
    log(`  wrote ${path.join(outDir, "baseline-meta.json")}`);
    return { outDir, meta };
  } finally {
    restoreFetch();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  run(parseArgs(process.argv.slice(2)), process.env).catch((err) => {
    console.error(err instanceof Error ? err.message : err);
    process.exit(1);
  });
}
