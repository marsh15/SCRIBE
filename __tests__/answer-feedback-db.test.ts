import { execFileSync } from "node:child_process";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { randomUUID } from "node:crypto";

/** Destructive migration/FK test. Only runs against DATABASE_URL_TEST whose database name contains `test`. */
const url = process.env.DATABASE_URL_TEST;
function hasPsql() {
  try {
    execFileSync("psql", ["--version"], { stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}
function databaseName(value: string | undefined) {
  if (!value) return "";
  try {
    return decodeURIComponent(new URL(value).pathname.replace(/^\//, ""));
  } catch {
    return "";
  }
}
const enabled = /test/i.test(databaseName(url)) && hasPsql();
const root = path.resolve(__dirname, "..");
const migration = path.join(root, "drizzle/0021_answer_feedback.sql");
const rollback = path.join(root, "drizzle/rollback/0021_answer_feedback.down.sql");

function psql(args: string[]) {
  return execFileSync("psql", [url!, "-v", "ON_ERROR_STOP=1", "-q", "-X", "-At", ...args], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function psqlTransaction(commands: string[]) {
  return psql(commands.flatMap((command) => ["-c", command]));
}

describe.skipIf(!enabled)("0021 answer feedback migration and referential integrity (real Postgres)", () => {
  const wsA = randomUUID();
  const wsB = randomUUID();
  const chatA = `fb-chat-a-${randomUUID()}`;
  const chatB = `fb-chat-b-${randomUUID()}`;
  const messageA = `fb-answer-a-${randomUUID()}`;
  const messageB = `fb-answer-b-${randomUUID()}`;
  const ragTrace = `fb-rag-${randomUUID()}`;
  const retrievalTrace = randomUUID();
  const userA = `fb-user-a-${randomUUID()}`;
  const userB = `fb-user-b-${randomUUID()}`;

  beforeAll(() => {
    psql(["-c", "DROP TABLE IF EXISTS public.answer_feedback CASCADE; DROP INDEX IF EXISTS public.chat_messages_id_chat_id_uq;"]);
    psql(["-f", migration]);
    psql(["-f", migration]); // A completed migration can be reapplied safely.
    psql(["-c", `
      INSERT INTO workspaces(id, kind, owner_user_id) VALUES ('${wsA}', 'eval', '${userA}'), ('${wsB}', 'eval', '${userB}');
      INSERT INTO chats(id, title, user_id, workspace_id) VALUES ('${chatA}', 'a', '${userA}', '${wsA}'), ('${chatB}', 'b', '${userB}', '${wsB}');
      INSERT INTO chat_messages(id, chat_id, role, content) VALUES ('${messageA}', '${chatA}', 'assistant', 'Answer A'), ('${messageB}', '${chatB}', 'assistant', 'Answer B');
      INSERT INTO rag_traces(id, chat_id, user_id, workspace_id, assistant_message_id, query, top_k, threshold)
        VALUES ('${ragTrace}', '${chatA}', '${userA}', '${wsA}', '${messageA}', 'query', 5, 0.1);
      INSERT INTO retrieval_traces(id, workspace_id, query) VALUES ('${retrievalTrace}', '${wsA}', 'query');
    `]);
  });

  afterAll(() => {
    if (!enabled) return;
    const errors: unknown[] = [];
    try {
      psql(["-c", `DELETE FROM chats WHERE id IN ('${chatA}', '${chatB}'); DELETE FROM workspaces WHERE id IN ('${wsA}', '${wsB}');`]);
    } catch (error) {
      errors.push(error);
    }
    try {
      psql(["-f", rollback]);
    } catch (error) {
      errors.push(error);
    }
    try {
      psql(["-f", migration]);
    } catch (error) {
      errors.push(error);
    }
    if (errors.length) throw new AggregateError(errors, "answer feedback test teardown failed");
  });

  it("rejects message/chat and chat/workspace mismatches at the database boundary", () => {
    expect(() => psql(["-c", `
      INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating)
      VALUES ('${wsA}', '${chatA}', '${messageB}', '${userA}', 'down');
    `])).toThrow();
    expect(() => psql(["-c", `
      INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating)
      VALUES ('${wsA}', '${chatB}', '${messageB}', '${userA}', 'down');
    `])).toThrow();
  });

  it("enforces workspace RLS and grants for scribe_app", () => {
    psqlTransaction([
      "BEGIN",
      "SET LOCAL ROLE scribe_app",
      `SET LOCAL app.workspace_id = '${wsA}'`,
      `INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating)
       VALUES ('${wsA}', '${chatA}', '${messageA}', '${userA}', 'up')`,
      "COMMIT",
    ]);

    expect(psqlTransaction([
      "BEGIN",
      "SET LOCAL ROLE scribe_app",
      `SET LOCAL app.workspace_id = '${wsA}'`,
      `SELECT count(*) FROM answer_feedback WHERE message_id = '${messageA}'`,
      "COMMIT",
    ])).toBe("1");
    expect(psqlTransaction([
      "BEGIN",
      "SET LOCAL ROLE scribe_app",
      `SET LOCAL app.workspace_id = '${wsB}'`,
      `SELECT count(*) FROM answer_feedback WHERE message_id = '${messageA}'`,
      "COMMIT",
    ])).toBe("0");
    expect(psqlTransaction([
      "BEGIN",
      "SET LOCAL ROLE scribe_app",
      `SELECT count(*) FROM answer_feedback WHERE message_id = '${messageA}'`,
      "COMMIT",
    ])).toBe("0");
    expect(() => psqlTransaction([
      "BEGIN",
      "SET LOCAL ROLE scribe_app",
      `SET LOCAL app.workspace_id = '${wsB}'`,
      `INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating)
       VALUES ('${wsA}', '${chatA}', '${messageA}', '${userA}', 'down')`,
      "COMMIT",
    ])).toThrow();
    expect(psql(["-c", `SELECT count(*) FROM answer_feedback WHERE message_id = '${messageA}'`])).toBe("1");
    psql(["-c", `DELETE FROM answer_feedback WHERE workspace_id = '${wsA}' AND message_id = '${messageA}' AND rating = 'up' AND rag_trace_id IS NULL AND retrieval_trace_id IS NULL`]);
  });

  it("enforces rating, reason subset, and comment length checks in Postgres", () => {
    const invalidInserts = [
      `INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating)
       VALUES ('${wsA}', '${chatA}', '${messageA}', '${userA}', 'neutral');`,
      `INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating, reasons)
       VALUES ('${wsA}', '${chatA}', '${messageA}', '${userA}', 'down', ARRAY['unlisted_reason']::text[]);`,
      `INSERT INTO answer_feedback(workspace_id, chat_id, message_id, user_id, rating, comment)
       VALUES ('${wsA}', '${chatA}', '${messageA}', '${userA}', 'down', '${"x".repeat(2001)}');`,
    ];
    for (const insert of invalidInserts) expect(() => psql(["-c", insert])).toThrow();
  });

  it("sets deleted trace links to null without clearing workspace and cascades on chat delete", () => {
    psql(["-c", `
      INSERT INTO answer_feedback(workspace_id, chat_id, message_id, rag_trace_id, retrieval_trace_id, user_id, rating)
      VALUES ('${wsA}', '${chatA}', '${messageA}', '${ragTrace}', '${retrievalTrace}', '${userA}', 'down');
      DELETE FROM rag_traces WHERE id = '${ragTrace}';
      DELETE FROM retrieval_traces WHERE id = '${retrievalTrace}';
    `]);
    expect(psql(["-c", `SELECT (rag_trace_id IS NULL)::int || ',' || (retrieval_trace_id IS NULL)::int || ',' || workspace_id::text FROM answer_feedback WHERE message_id = '${messageA}'`]))
      .toBe(`1,1,${wsA}`);
    psql(["-c", `DELETE FROM chats WHERE id = '${chatA}';`]);
    expect(psql(["-c", `SELECT count(*) FROM answer_feedback WHERE message_id = '${messageA}'`])).toBe("0");
  });

  it("rolls back cleanly and can be applied again", () => {
    psql(["-f", rollback]);
    expect(psql(["-c", "SELECT to_regclass('public.answer_feedback') IS NULL"])).toBe("t");
    psql(["-f", migration]);
    expect(psql(["-c", "SELECT to_regclass('public.answer_feedback') IS NOT NULL"])).toBe("t");
  });
});
