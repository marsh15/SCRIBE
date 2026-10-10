import { execFileSync } from "node:child_process";
import path from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { psqlExecutor, runBackfill } from "../scripts/backfill-legacy-generations";

/**
 * Real-Postgres test for migrations 0010/0011 and the legacy backfill. Skipped
 * unless TEST_DATABASE_URL points at a pgvector-enabled throwaway database and
 * `psql` is on PATH. Example (compose db already has pgvector):
 *
 *   docker compose up -d db
 *   docker exec scribe-db-1 psql -U scribe -d scribe -c 'CREATE DATABASE t08'
 *   TEST_DATABASE_URL=postgresql://scribe:scribe@localhost:54329/t08 npm test
 *
 * Everything runs inside the private schema `t08_backfill_test` (search_path),
 * so it never collides with other tests sharing the database. The schema is
 * dropped and recreated each run. Only the legacy tables the migrations touch
 * are recreated here; full-schema equivalence (prior Drizzle schema + 0010/0011
 * == `drizzle-kit export` of lib/db-schema.ts) is checked in T08-notes.md.
 */
const baseUrl = process.env.TEST_DATABASE_URL;

function hasPsql() {
  try {
    execFileSync("psql", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const enabled = Boolean(baseUrl) && hasPsql();
const SCHEMA = "t08_backfill_test";
const url = enabled
  ? `${baseUrl}${baseUrl!.includes("?") ? "&" : "?"}options=-csearch_path%3D${SCHEMA}%2Cpublic`
  : "";
const migration = (f: string) => path.resolve(__dirname, "../drizzle", f);

function psql(args: string[]) {
  return execFileSync("psql", [url, "-v", "ON_ERROR_STOP=1", "-q", "-X", "-At", ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, PGOPTIONS: "-c client_min_messages=warning" },
  }).trim();
}
const q = (sql: string) => psql(["-c", sql]);

const LEGACY_DDL = `
CREATE TABLE workspaces (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), kind text NOT NULL,
  owner_user_id text, created_at timestamp DEFAULT now() NOT NULL);
CREATE TABLE files (id serial PRIMARY KEY, name text NOT NULL, type text NOT NULL, size integer NOT NULL,
  user_id text NOT NULL, workspace_id uuid NOT NULL REFERENCES workspaces(id), file_data text,
  extracted_text text, storage_key text, storage_url text, status text DEFAULT 'ready' NOT NULL,
  processing_error text, text_bytes integer, created_at timestamp DEFAULT now() NOT NULL,
  updated_at timestamp DEFAULT now() NOT NULL);
CREATE TABLE documents (id serial PRIMARY KEY, file_id integer NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL, content text NOT NULL, metadata jsonb, embeddings vector(3072) NOT NULL);
CREATE UNIQUE INDEX documents_file_id_chunk_index_unique ON documents (file_id, chunk_index);
CREATE TABLE chats (id text PRIMARY KEY, title text NOT NULL, user_id text NOT NULL,
  workspace_id uuid NOT NULL REFERENCES workspaces(id));
CREATE TABLE chat_messages (id text PRIMARY KEY, chat_id text NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role text NOT NULL, content text NOT NULL);
CREATE TABLE ingestion_jobs (id serial PRIMARY KEY, file_id integer NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  status text DEFAULT 'queued' NOT NULL, attempts integer DEFAULT 0 NOT NULL, last_error text,
  next_retry_at timestamp);
`;

// A deterministic, real 3072-dim vector per (file, chunk).
const vec = (f: string, i: string) =>
  `(SELECT array_agg((((${f}*7 + ${i}*13 + g) % 17)::real / 17.0) + 0.01) FROM generate_series(1,3072) g)::vector`;

const FIXTURE = `
INSERT INTO workspaces (id, kind, owner_user_id) VALUES
  ('11111111-1111-4111-8111-111111111111','personal','u1'),
  ('22222222-2222-4222-8222-222222222222','personal','u2');
INSERT INTO files (id, name, type, size, user_id, workspace_id, status, storage_key, file_data, created_at) VALUES
  (1,'a.pdf','application/pdf',100,'u1','11111111-1111-4111-8111-111111111111','ready','blob/a.pdf',NULL,'2025-01-02 03:04:05'),
  (2,'b.txt','text/plain',5,'u1','11111111-1111-4111-8111-111111111111','ready',NULL,'aGVsbG8=',now()),
  (3,'c.csv','text/csv',50,'u2','22222222-2222-4222-8222-222222222222','failed',NULL,NULL,now()),
  (4,'d.docx','application/msword',70,'u2','22222222-2222-4222-8222-222222222222','processing','blob/d.docx',NULL,now()),
  (5,'e.txt','text/plain',9,'u2','22222222-2222-4222-8222-222222222222','ready','blob/e.txt',NULL,now());
INSERT INTO documents (file_id, chunk_index, content, metadata, embeddings)
  SELECT f, i, 'chunk ' || f || '-' || i, '{"page":1}'::jsonb, ${vec("f", "i")}
  FROM (VALUES (1,0),(1,1),(1,2),(2,0),(2,1),(4,0),(5,0)) AS t(f,i);
SELECT setval('files_id_seq', 10);
`;

describe.skipIf(!enabled)("0010/0011 migrations + legacy backfill (real Postgres)", () => {
  afterAll(() => {
    // Leave no private schema behind: catalog queries in other tests assume only `public`.
    psql(["-c", `DROP SCHEMA IF EXISTS ${SCHEMA} CASCADE;`]);
  });

  it("migrates twice, backfills twice with matching counts, and leaves documents untouched", async () => {
    psql(["-c", `DROP SCHEMA IF EXISTS ${SCHEMA} CASCADE; CREATE SCHEMA ${SCHEMA};
                 CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;`]);
    psql(["-c", LEGACY_DDL]);
    psql(["-c", FIXTURE]); // legacy rows exist BEFORE the new migrations run
    for (const run of [1, 2]) {
      psql(["-f", migration("0010_source_versions_generations.sql")]);
      psql(["-f", migration("0011_seed_profiles.sql")]);
      expect(Number(q("SELECT count(*) FROM index_profiles")), `run ${run}`).toBe(2);
      expect(Number(q("SELECT count(*) FROM embedding_profiles")), `run ${run}`).toBe(2);
      expect(Number(q("SELECT count(*) FROM chunk_profiles")), `run ${run}`).toBe(2);
    }
    q("UPDATE files SET deleted_at = now() WHERE id = 5");

    const docsBefore = q("SELECT count(*) || ':' || sum(length(content)) || ':' || md5(string_agg(embeddings::text, '' ORDER BY id)) FROM documents");
    const exec = psqlExecutor(url);

    // Dry run writes nothing.
    const dry = await runBackfill(exec, { batchSize: 2, afterId: 0, dryRun: true });
    expect(dry.filesSeen).toBe(4); // file 5 is soft-deleted
    expect(q("SELECT count(*) FROM source_versions")).toBe("0");

    const first = await runBackfill(exec, { batchSize: 2, afterId: 0, dryRun: false });
    expect(first.batches).toBe(2);
    expect(first.verification).toMatchObject({
      filesWithDocuments: 3,
      legacyActiveGenerations: 3,
      documents: 6, // file 5's chunk belongs to a deleted file and is excluded
      legacyChunks: 6,
      legacyEmbeddings: 6,
      ok: true,
    });

    const snapshot = () =>
      q(`SELECT (SELECT count(*) FROM source_versions) || ',' || (SELECT count(*) FROM index_generations) || ',' ||
                (SELECT count(*) FROM chunks) || ',' || (SELECT count(*) FROM chunk_embeddings_3072) || ',' ||
                (SELECT count(*) FROM active_indexes)`);
    const afterFirst = snapshot();
    expect(afterFirst).toBe("4,3,6,6,3");

    const second = await runBackfill(exec, { batchSize: 2, afterId: 0, dryRun: false });
    expect(second.batches).toBe(0); // nothing pending: resumable / idempotent
    expect(snapshot()).toBe(afterFirst);
    expect(second.verification?.ok).toBe(true);

    // Forcing a re-scan from the start must also be a no-op on data.
    q("UPDATE files SET desired_version_id = NULL WHERE id = 3");
    await runBackfill(exec, { batchSize: 1, afterId: 0, dryRun: false });
    expect(snapshot()).toBe(afterFirst);

    // Field mapping.
    expect(q("SELECT sha256 FROM source_versions WHERE source_id = 2")).toBe(
      "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824" // sha256("hello")
    );
    expect(q("SELECT coalesce(sha256,'null') FROM source_versions WHERE source_id = 1")).toBe("null");
    expect(q("SELECT storage_key || '|' || mime_type || '|' || upload_state FROM source_versions WHERE source_id = 1")).toBe(
      "blob/a.pdf|application/pdf|stored"
    );
    expect(q("SELECT upload_state FROM source_versions WHERE source_id = 3")).toBe("failed");
    expect(q("SELECT count(*) FROM source_versions WHERE source_id = 5")).toBe("0");
    expect(q("SELECT created_at::text FROM source_versions WHERE source_id = 1")).toContain("2025-01-02 03:04:05");
    // Pointers: file 3 has no chunks, so desired is set but active stays null.
    expect(q("SELECT (desired_version_id IS NOT NULL) || ',' || (active_version_id IS NOT NULL) FROM files WHERE id = 3")).toBe("true,false");
    expect(q("SELECT count(*) FROM files WHERE id IN (1,2,4) AND active_version_id IS NOT NULL AND desired_version_id = active_version_id")).toBe("3");
    expect(q("SELECT status || ',' || expected_chunks || ',' || embedded_chunks FROM index_generations g JOIN index_profiles p ON p.id = g.index_profile_id WHERE g.source_id = 1 AND p.name = 'legacy-v1'")).toBe("ACTIVE,3,3");
    // chunk content, hash, and vectors copied exactly.
    expect(q(`SELECT count(*) FROM chunks c JOIN index_generations g ON g.id = c.generation_id
              JOIN documents d ON d.file_id = g.source_id AND d.chunk_index = c.chunk_index
              JOIN chunk_embeddings_3072 e ON e.chunk_id = c.id
              WHERE d.content = c.content AND d.embeddings = e.embedding
                AND c.content_hash = encode(sha256(convert_to(d.content,'UTF8')),'hex')
                AND c.token_count IS NULL AND c.heading_text IS NULL`)).toBe("6");

    // The legacy documents table is untouched.
    expect(q("SELECT count(*) || ':' || sum(length(content)) || ':' || md5(string_agg(embeddings::text, '' ORDER BY id)) FROM documents")).toBe(docsBefore);

    // A file added later is picked up incrementally.
    q(`INSERT INTO files (id, name, type, size, user_id, workspace_id, status)
       VALUES (6,'f.txt','text/plain',1,'u1','11111111-1111-4111-8111-111111111111','ready');
       INSERT INTO documents (file_id, chunk_index, content, embeddings) SELECT 6, 0, 'late', ${vec("6", "0")}`);
    const third = await runBackfill(exec, { batchSize: 10, afterId: 0, dryRun: false });
    expect(third.filesSeen).toBe(1);
    expect(third.verification).toMatchObject({ filesWithDocuments: 4, legacyActiveGenerations: 4, documents: 7, ok: true });
  }, 120_000);
});
