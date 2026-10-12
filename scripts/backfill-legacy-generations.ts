/**
 * Scribe 2.0 T08: backfill every non-deleted legacy `files` row as Source
 * version 1 plus one ACTIVE `legacy-v1` index generation built from `documents`.
 *
 *   npx tsx scripts/backfill-legacy-generations.ts [--batch-size 50] [--after-id 0] [--dry-run] [--psql]
 *
 * - Idempotent and resumable: every statement is ON CONFLICT DO NOTHING or
 *   guarded by status, each batch of files commits in ONE transaction, and a
 *   re-run only picks up files that are not finished. `--after-id N` resumes a
 *   scan from a file id (not required for correctness).
 * - `documents` is only ever read. Nothing is deleted or updated there.
 * - Connection: NEON_DATABASE_URL (from .env.local or the environment, same as
 *   lib/db-config.ts). Neon URLs run over the neon HTTP driver, one transaction
 *   per batch. localhost URLs (or --psql) run through the `psql` binary with
 *   `--single-transaction`, which is how the vitest suite exercises the same SQL.
 * - Needs migrations 0010 and 0011 (tables + `legacy-v1` profile) applied first.
 * - sha256: computed only for legacy rows that still hold the base64 original in
 *   `files.file_data` (cheaply available). Blob-backed originals are NOT fetched;
 *   their sha256 stays NULL (T19 dedupe treats NULL as "unknown").
 */
import { spawnSync } from "node:child_process";
import { pathToFileURL } from "node:url";

export const LEGACY_INDEX_PROFILE_NAME = "legacy-v1";

export interface BackfillExecutor {
  /** Run a read query; returns the first column of every row as a string. */
  scalars(sql: string): Promise<string[]>;
  /** Run statements atomically (one transaction). */
  transaction(statements: string[]): Promise<void>;
}

export interface BackfillOptions {
  batchSize: number;
  afterId: number;
  dryRun: boolean;
  log?: (line: string) => void;
}

export interface BackfillReport {
  batches: number;
  filesSeen: number;
  filesWithChunks: number;
  dryRun: boolean;
  verification: Verification | null;
}

export interface Verification {
  filesWithDocuments: number;
  legacyActiveGenerations: number;
  documents: number;
  legacyChunks: number;
  legacyEmbeddings: number;
  ok: boolean;
}

const legacyProfileId = `(SELECT id FROM index_profiles WHERE name = '${LEGACY_INDEX_PROFILE_NAME}')`;

function idList(ids: number[]): string {
  if (ids.some((n) => !Number.isSafeInteger(n))) throw new Error("non-integer file id");
  return ids.join(",");
}

/** The set-based SQL for one batch of file ids. Order matters; all in one txn. */
export function batchStatements(ids: number[]): string[] {
  const list = idList(ids);
  return [
    // 1. Source version 1 per file.
    `INSERT INTO source_versions
       (source_id, workspace_id, version_number, storage_key, sha256, size_bytes,
        mime_type, upload_state, created_by, created_at)
     SELECT f.id, f.workspace_id, 1, f.storage_key,
            CASE WHEN f.file_data IS NOT NULL AND f.file_data ~ '^[A-Za-z0-9+/=[:space:]]+$'
                 THEN encode(sha256(decode(f.file_data, 'base64')), 'hex') END,
            f.size, f.type,
            CASE WHEN f.status = 'uploading' THEN 'uploading'
                 WHEN f.status = 'failed' AND f.storage_key IS NULL AND f.file_data IS NULL THEN 'failed'
                 ELSE 'stored' END,
            f.user_id, f.created_at AT TIME ZONE 'UTC'
     FROM files f
     WHERE f.id IN (${list}) AND f.deleted_at IS NULL
     ON CONFLICT (source_id, version_number) DO NOTHING`,

    // 2. One BUILDING generation per file that has legacy chunks.
    `INSERT INTO index_generations
       (source_id, source_version_id, index_profile_id, workspace_id, status)
     SELECT f.id, v.id, ${legacyProfileId}, f.workspace_id, 'BUILDING'
     FROM files f
     JOIN source_versions v ON v.source_id = f.id AND v.version_number = 1
     WHERE f.id IN (${list}) AND f.deleted_at IS NULL
       AND EXISTS (SELECT 1 FROM documents d WHERE d.file_id = f.id)
     ON CONFLICT (source_version_id, index_profile_id) DO NOTHING`,

    // 3. Chunks from documents (only for generations not yet finished).
    `INSERT INTO chunks
       (generation_id, source_version_id, workspace_id, chunk_index, content,
        heading_text, content_hash, token_count)
     SELECT g.id, g.source_version_id, g.workspace_id, d.chunk_index, d.content,
            NULL, encode(sha256(convert_to(d.content, 'UTF8')), 'hex'), NULL
     FROM index_generations g
     JOIN documents d ON d.file_id = g.source_id
     WHERE g.source_id IN (${list}) AND g.status = 'BUILDING'
       AND g.index_profile_id = ${legacyProfileId}
     ON CONFLICT (generation_id, chunk_index) DO NOTHING`,

    // 4. Copy the 3072-dim vectors (exact search only; no ANN index exists).
    `INSERT INTO chunk_embeddings_3072 (chunk_id, workspace_id, embedding_profile_id, embedding)
     SELECT c.id, c.workspace_id, ip.embedding_profile_id, d.embeddings
     FROM index_generations g
     JOIN chunks c ON c.generation_id = g.id
     JOIN documents d ON d.file_id = g.source_id AND d.chunk_index = c.chunk_index
     JOIN index_profiles ip ON ip.id = g.index_profile_id
     WHERE g.source_id IN (${list}) AND g.status = 'BUILDING'
       AND g.index_profile_id = ${legacyProfileId}
     ON CONFLICT (chunk_id) DO NOTHING`,

    // 5. Activate generations whose chunks and embeddings fully match documents.
    `UPDATE index_generations g
     SET expected_chunks = x.n_docs, embedded_chunks = x.n_emb, status = 'ACTIVE',
         ready_at = now(), activated_at = now(), updated_at = now()
     FROM (
       SELECT g2.id,
              (SELECT count(*) FROM documents d WHERE d.file_id = g2.source_id) AS n_docs,
              (SELECT count(*) FROM chunks c WHERE c.generation_id = g2.id) AS n_chunks,
              (SELECT count(*) FROM chunks c JOIN chunk_embeddings_3072 e ON e.chunk_id = c.id
                WHERE c.generation_id = g2.id) AS n_emb
       FROM index_generations g2
       WHERE g2.source_id IN (${list}) AND g2.status = 'BUILDING'
         AND g2.index_profile_id = ${legacyProfileId}
     ) x
     WHERE g.id = x.id AND x.n_docs = x.n_chunks AND x.n_docs = x.n_emb`,

    // 6. Pointer row (PK source_id + index profile makes this idempotent).
    `INSERT INTO active_indexes (source_id, index_profile_id, generation_id, workspace_id)
     SELECT g.source_id, g.index_profile_id, g.id, g.workspace_id
     FROM index_generations g
     WHERE g.source_id IN (${list}) AND g.status = 'ACTIVE'
       AND g.index_profile_id = ${legacyProfileId}
     ON CONFLICT (source_id, index_profile_id) DO NOTHING`,

    // 7. files pointers. desired always; active only when a legacy generation is active.
    `UPDATE files f
     SET desired_version_id = COALESCE(f.desired_version_id, v.id),
         active_version_id = CASE
           WHEN EXISTS (SELECT 1 FROM active_indexes ai
                        WHERE ai.source_id = f.id AND ai.index_profile_id = ${legacyProfileId})
           THEN COALESCE(f.active_version_id, v.id) ELSE f.active_version_id END
     FROM source_versions v
     WHERE v.source_id = f.id AND v.version_number = 1
       AND f.id IN (${list}) AND f.deleted_at IS NULL`,
  ];
}

const pendingFilter = `f.deleted_at IS NULL AND (
  NOT EXISTS (SELECT 1 FROM source_versions v WHERE v.source_id = f.id AND v.version_number = 1)
  OR f.desired_version_id IS NULL
  OR (f.active_version_id IS NULL AND EXISTS (SELECT 1 FROM documents d WHERE d.file_id = f.id))
)`;

export async function verify(exec: BackfillExecutor): Promise<Verification> {
  const [row] = await exec.scalars(
    `SELECT concat_ws(',',
       (SELECT count(DISTINCT d.file_id) FROM documents d JOIN files f ON f.id = d.file_id WHERE f.deleted_at IS NULL),
       (SELECT count(*) FROM index_generations g JOIN files f ON f.id = g.source_id
          WHERE g.status = 'ACTIVE' AND g.index_profile_id = ${legacyProfileId} AND f.deleted_at IS NULL),
       (SELECT count(*) FROM documents d JOIN files f ON f.id = d.file_id WHERE f.deleted_at IS NULL),
       (SELECT count(*) FROM chunks c JOIN index_generations g ON g.id = c.generation_id
          WHERE g.index_profile_id = ${legacyProfileId}),
       (SELECT count(*) FROM chunk_embeddings_3072 e JOIN chunks c ON c.id = e.chunk_id
          JOIN index_generations g ON g.id = c.generation_id WHERE g.index_profile_id = ${legacyProfileId})
     )`
  );
  const [filesWithDocuments, legacyActiveGenerations, documents, legacyChunks, legacyEmbeddings] =
    row.split(",").map(Number);
  return {
    filesWithDocuments,
    legacyActiveGenerations,
    documents,
    legacyChunks,
    legacyEmbeddings,
    ok:
      filesWithDocuments === legacyActiveGenerations &&
      documents === legacyChunks &&
      documents === legacyEmbeddings,
  };
}

export async function runBackfill(
  exec: BackfillExecutor,
  opts: BackfillOptions
): Promise<BackfillReport> {
  const log = opts.log ?? (() => undefined);
  if (!Number.isInteger(opts.batchSize) || opts.batchSize < 1) {
    throw new Error("--batch-size must be a positive integer");
  }
  const profile = await exec.scalars(
    `SELECT id FROM index_profiles WHERE name = '${LEGACY_INDEX_PROFILE_NAME}'`
  );
  if (profile.length === 0) {
    throw new Error(
      `index profile '${LEGACY_INDEX_PROFILE_NAME}' not found: apply drizzle/0010 and 0011 first`
    );
  }

  const report: BackfillReport = {
    batches: 0,
    filesSeen: 0,
    filesWithChunks: 0,
    dryRun: opts.dryRun,
    verification: null,
  };
  let cursor = opts.afterId;
  for (;;) {
    const ids = (
      await exec.scalars(
        `SELECT f.id FROM files f WHERE f.id > ${Number(cursor)} AND ${pendingFilter}
         ORDER BY f.id LIMIT ${opts.batchSize}`
      )
    ).map(Number);
    if (ids.length === 0) break;
    cursor = ids[ids.length - 1];
    report.batches += 1;
    report.filesSeen += ids.length;
    const [withChunks] = await exec.scalars(
      `SELECT count(DISTINCT file_id) FROM documents WHERE file_id IN (${idList(ids)})`
    );
    report.filesWithChunks += Number(withChunks);
    if (opts.dryRun) {
      log(`[dry-run] batch ${report.batches}: files ${ids[0]}..${cursor} (${ids.length} files, ${withChunks} with chunks)`);
      continue;
    }
    await exec.transaction(batchStatements(ids));
    log(`batch ${report.batches}: files ${ids[0]}..${cursor} (${ids.length} files, ${withChunks} with chunks) committed`);
  }
  if (!opts.dryRun) report.verification = await verify(exec);
  return report;
}

// ---------------------------------------------------------------- executors

export function psqlExecutor(url: string): BackfillExecutor {
  const run = (input: string, extra: string[]) => {
    const res = spawnSync("psql", [url, "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1", ...extra, "-f", "-"], {
      input,
      encoding: "utf8",
      maxBuffer: 256 * 1024 * 1024,
    });
    if (res.status !== 0) throw new Error(`psql failed: ${res.stderr || res.error}`);
    return res.stdout;
  };
  return {
    async scalars(sql) {
      return run(`${sql};\n`, [])
        .split("\n")
        .filter((l) => l.length > 0);
    },
    async transaction(statements) {
      run(statements.map((s) => `${s};`).join("\n"), ["--single-transaction"]);
    },
  };
}

export async function neonExecutor(): Promise<BackfillExecutor> {
  // Reuse the project's db config (loads .env.local, reads NEON_DATABASE_URL).
  const { sql } = await import("../lib/db-config");
  return {
    async scalars(text) {
      const rows = (await sql.query(text, [], { arrayMode: true })) as unknown[][];
      return rows.map((r) => String(r[0]));
    },
    async transaction(statements) {
      await sql.transaction((txn) => statements.map((s) => txn.query(s)));
    },
  };
}

// ---------------------------------------------------------------------- CLI

function parseArgs(argv: string[]) {
  const out = { batchSize: 50, afterId: 0, dryRun: false, psql: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") out.dryRun = true;
    else if (a === "--psql") out.psql = true;
    else if (a === "--batch-size") out.batchSize = Number(argv[++i]);
    else if (a === "--after-id") out.afterId = Number(argv[++i]);
    else throw new Error(`unknown argument ${a}`);
  }
  return out;
}

async function main() {
  const { config } = await import("dotenv");
  config({ path: ".env.local", quiet: true });
  const args = parseArgs(process.argv.slice(2));
  const url = process.env.NEON_DATABASE_URL;
  if (!url) throw new Error("NEON_DATABASE_URL is not set (point it at the target database or Neon branch)");
  const local = /@(localhost|127\.0\.0\.1|\[::1\])(:|\/)/.test(url);
  const exec = args.psql || local ? psqlExecutor(url) : await neonExecutor();
  const report = await runBackfill(exec, {
    batchSize: args.batchSize,
    afterId: args.afterId,
    dryRun: args.dryRun,
    log: (l) => console.log(l),
  });
  console.log(JSON.stringify(report, null, 2));
  if (report.verification && !report.verification.ok) {
    console.error("VERIFICATION FAILED: counts do not match");
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err);
    process.exit(1);
  });
}
