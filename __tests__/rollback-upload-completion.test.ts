import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

// Runs the exact upload-completion statement from lib/ingestion/source-intake.ts against a real Postgres.
// Set TEST_DATABASE_URL to a throwaway database whose name contains "test" (1.0 schema, or 1.0 schema + drizzle 0008-0020).
// Needs `psql` on PATH. Skipped when TEST_DATABASE_URL is not set.
const url = process.env.TEST_DATABASE_URL;

function psql(sqlText: string) {
  return execFileSync("psql", [url!, "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-c", sqlText], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

const literal = (value: string | number) => `'${String(value).replace(/'/g, "''")}'`;

function transitionStatement(fileId: number, userId: string, conflictClause?: string) {
  const source = readFileSync(path.join(__dirname, "../lib/ingestion/source-intake.ts"), "utf8");
  const match = source.match(/const transitioned = await sql`([\s\S]*?)`;/);
  if (!match) throw new Error("transitionToQueued statement not found in source-intake.ts");
  let statement = match[1]
    .replace("${blob.pathname}", literal("uploads/test.txt"))
    .replace("${blob.url}", literal("https://blob.example/test.txt"))
    .replace("${source.id}", String(fileId))
    .replace("${source.userId}", literal(userId));
  if (conflictClause) statement = statement.replace(/ON CONFLICT DO NOTHING/, conflictClause);
  if (/\$\{/.test(statement)) throw new Error("unsubstituted placeholder in statement");
  return statement;
}

describe.skipIf(!url)("1.0 upload completion on a real Postgres", () => {
  it("only runs against a throwaway database", () => {
    expect(new URL(url!).pathname).toMatch(/test/);
  });

  function newUploadingFile(userId: string) {
    return Number(
      psql(
        `INSERT INTO files (name, type, size, user_id, status) VALUES ('t.txt', 'text/plain', 5, ${literal(userId)}, 'uploading') RETURNING id`
      ).split("\n")[0]
    );
  }
  const jobCount = (fileId: number) =>
    Number(psql(`SELECT count(*) FROM ingestion_jobs WHERE file_id = ${fileId}`));

  it("queues one job, and a second completion for the same file queues nothing", () => {
    const userId = `user_rb_${Date.now()}`;
    const fileId = newUploadingFile(userId);
    const statement = transitionStatement(fileId, userId);

    expect(psql(statement)).toBe(String(fileId));
    expect(jobCount(fileId)).toBe(1);
    expect(psql(`SELECT status FROM files WHERE id = ${fileId}`)).toBe("queued");

    // Second run: the file is no longer 'uploading', so the CTE yields no row and nothing is returned or inserted.
    expect(psql(statement)).toBe("");
    expect(jobCount(fileId)).toBe(1);
  });

  it("dedupes a job that already exists for the file (the ON CONFLICT path)", () => {
    const userId = `user_rb_${Date.now()}_b`;
    const fileId = newUploadingFile(userId);
    psql(`INSERT INTO ingestion_jobs (file_id, status, attempts) VALUES (${fileId}, 'queued', 0)`);

    expect(psql(transitionStatement(fileId, userId))).toBe("");
    expect(jobCount(fileId)).toBe(1);
  });

  it("documents why the old ON CONFLICT (file_id) form cannot be used on the migrated schema", () => {
    const oldIndexExists =
      psql(`SELECT count(*) FROM pg_indexes WHERE indexname = 'ingestion_jobs_file_id_unique'`) === "1";
    const userId = `user_rb_${Date.now()}_c`;
    const fileId = newUploadingFile(userId);
    const unpatched = transitionStatement(fileId, userId, 'ON CONFLICT ("file_id") DO NOTHING');

    if (oldIndexExists) {
      expect(psql(unpatched)).toBe(String(fileId));
    } else {
      expect(() => psql(unpatched)).toThrow(/no unique or exclusion constraint matching the ON CONFLICT specification/);
    }
  });
});
