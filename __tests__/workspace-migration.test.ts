import { execFileSync } from "node:child_process";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Real-Postgres migration test. Skipped unless TEST_DATABASE_URL points at a
 * throwaway database AND `psql` is on PATH. Example:
 *
 *   docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=pw pgvector/pgvector:pg17
 *   TEST_DATABASE_URL=postgres://postgres:pw@localhost:55432/postgres npm test
 *
 * The test DROPs and recreates the tables files/chats/rag_traces/workspaces/
 * workspace_members in the target database, so never point it at real data.
 */
const url = process.env.TEST_DATABASE_URL;

function hasPsql() {
  try {
    execFileSync("psql", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

const enabled = Boolean(url) && hasPsql();
const migration = path.resolve(__dirname, "../drizzle/0008_workspaces.sql");

function psql(args: string[]) {
  return execFileSync("psql", [url!, "-v", "ON_ERROR_STOP=1", "-q", "-X", "-At", ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  }).trim();
}

describe.skipIf(!enabled)("0008_workspaces migration (real Postgres)", () => {
  it("backfills once, is safe to run twice, and enforces constraints", () => {
    psql([
      "-c",
      `DROP TABLE IF EXISTS workspace_members, rag_traces, chats, files, workspaces CASCADE;
       CREATE TABLE files (id serial PRIMARY KEY, user_id text NOT NULL);
       CREATE TABLE chats (id text PRIMARY KEY, user_id text NOT NULL);
       CREATE TABLE rag_traces (id text PRIMARY KEY, user_id text NOT NULL);
       INSERT INTO files (user_id) VALUES ('a'), ('a'), ('b');
       INSERT INTO chats VALUES ('c1', 'b'), ('c2', 'c');
       INSERT INTO rag_traces VALUES ('t1', 'd');`,
    ]);

    psql(["-f", migration]);
    psql(["-f", migration]); // second run must be a harmless no-op

    expect(psql(["-c", "SELECT count(*) FROM workspaces"])).toBe("4");
    expect(psql(["-c", "SELECT count(*) FROM workspace_members"])).toBe("4");
    for (const t of ["files", "chats", "rag_traces"]) {
      expect(psql(["-c", `SELECT count(*) FROM ${t} WHERE workspace_id IS NULL`])).toBe("0");
    }
    // Each file row points at its own user's workspace.
    expect(
      psql([
        "-c",
        `SELECT count(*) FROM files f JOIN workspaces w ON w.id = f.workspace_id
         WHERE w.owner_user_id <> f.user_id`,
      ])
    ).toBe("0");

    // NOT NULL is enforced after backfill.
    expect(() =>
      psql(["-c", "INSERT INTO files (user_id) VALUES ('z')"])
    ).toThrow();
    // At most one personal workspace per user; the racing insert is a no-op.
    expect(
      psql([
        "-c",
        `INSERT INTO workspaces (kind, owner_user_id) VALUES ('personal', 'a') ON CONFLICT DO NOTHING;
         SELECT count(*) FROM workspaces WHERE owner_user_id = 'a'`,
      ])
    ).toBe("1");
    expect(() =>
      psql(["-c", "INSERT INTO workspaces (kind, owner_user_id) VALUES ('bogus', 'a')"])
    ).toThrow();
  });
});
