import { readFileSync } from "node:fs";
import path from "node:path";
import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";
import { answerFeedback } from "@/lib/db-schema";

const ROOT = path.resolve(__dirname, "..");
const migration = readFileSync(path.join(ROOT, "drizzle/0021_answer_feedback.sql"), "utf8");
const rollback = readFileSync(path.join(ROOT, "drizzle/rollback/0021_answer_feedback.down.sql"), "utf8");
const journal = JSON.parse(readFileSync(path.join(ROOT, "drizzle/meta/_journal.json"), "utf8")) as {
  entries: Array<{ idx: number; when: number; tag: string }>;
};

describe("answer feedback schema contract", () => {
  it("exports the new table with checked feedback fields and workspace index", () => {
    const config = getTableConfig(answerFeedback);
    expect(config.name).toBe("answer_feedback");
    expect(config.columns.map((column) => column.name)).toEqual([
      "id", "workspace_id", "chat_id", "message_id", "rag_trace_id", "retrieval_trace_id",
      "user_id", "rating", "reasons", "comment", "created_at",
    ]);
    expect(config.checks.map((check) => check.name)).toEqual([
      "answer_feedback_rating_check",
      "answer_feedback_reasons_check",
      "answer_feedback_comment_length_check",
    ]);
    expect(config.indexes.map((index) => index.config.name)).toContain("answer_feedback_workspace_created_at_index");
  });

  it("journals the idempotent migration, RLS policy, grants, and rollback", () => {
    expect(journal.entries.at(-1)).toEqual({ idx: 21, version: "7", when: 1784937600001, tag: "0021_answer_feedback", breakpoints: true });
    expect(migration).toContain("CREATE TABLE IF NOT EXISTS public.answer_feedback");
    expect(migration).toContain("CREATE UNIQUE INDEX IF NOT EXISTS chat_messages_id_chat_id_uq");
    expect(migration).toContain("CREATE UNIQUE INDEX IF NOT EXISTS rag_traces_id_workspace_uq");
    expect(migration).toContain("FOREIGN KEY (message_id, chat_id)");
    expect(migration).toContain("FOREIGN KEY (chat_id, workspace_id)");
    expect(migration).toContain("ENABLE ROW LEVEL SECURITY");
    expect(migration).toContain("NO FORCE ROW LEVEL SECURITY");
    expect(migration).toContain("workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid");
    expect(migration).toContain("GRANT SELECT, INSERT, UPDATE, DELETE ON public.answer_feedback TO scribe_app");
    expect(migration).toContain("GRANT USAGE, SELECT ON SEQUENCE public.answer_feedback_id_seq TO scribe_app");
    expect(rollback).toContain("DROP TABLE IF EXISTS public.answer_feedback CASCADE");
    expect(rollback).toContain("DROP INDEX IF EXISTS public.chat_messages_id_chat_id_uq");
    expect(rollback).toContain("DROP INDEX IF EXISTS public.rag_traces_id_workspace_uq");
    expect(rollback).toContain("created_at = 1784937600001");
  });
});
