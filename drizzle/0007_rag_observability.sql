CREATE TABLE IF NOT EXISTS "rag_traces" (
  "id" text PRIMARY KEY NOT NULL,
  "chat_id" text NOT NULL,
  "user_id" text NOT NULL,
  "user_message_id" text,
  "assistant_message_id" text,
  "query" text NOT NULL,
  "status" text DEFAULT 'retrieved' NOT NULL,
  "embedding_ms" integer DEFAULT 0 NOT NULL,
  "retrieval_ms" integer DEFAULT 0 NOT NULL,
  "generation_ms" integer,
  "total_ms" integer,
  "top_k" integer NOT NULL,
  "threshold" double precision NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "rag_trace_chunks" (
  "id" serial PRIMARY KEY NOT NULL,
  "trace_id" text NOT NULL,
  "document_id" integer NOT NULL,
  "file_id" integer NOT NULL,
  "file_name" text NOT NULL,
  "chunk_index" integer NOT NULL,
  "similarity" double precision NOT NULL,
  "rank" integer NOT NULL,
  "content_preview" text NOT NULL,
  "metadata" jsonb
);

CREATE TABLE IF NOT EXISTS "rag_evaluations" (
  "id" serial PRIMARY KEY NOT NULL,
  "trace_id" text NOT NULL,
  "status" text DEFAULT 'completed' NOT NULL,
  "judge_model" text NOT NULL,
  "groundedness_score" double precision,
  "answer_relevance_score" double precision,
  "citation_support_score" double precision,
  "overall_score" double precision,
  "verdict" text,
  "rationale" text,
  "error" text,
  "created_at" timestamp DEFAULT now() NOT NULL
);

DO $$ BEGIN
 ALTER TABLE "rag_traces" ADD CONSTRAINT "rag_traces_chat_id_chats_id_fk"
  FOREIGN KEY ("chat_id") REFERENCES "public"."chats"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
 ALTER TABLE "rag_traces" ADD CONSTRAINT "rag_traces_user_message_id_chat_messages_id_fk"
  FOREIGN KEY ("user_message_id") REFERENCES "public"."chat_messages"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
 ALTER TABLE "rag_traces" ADD CONSTRAINT "rag_traces_assistant_message_id_chat_messages_id_fk"
  FOREIGN KEY ("assistant_message_id") REFERENCES "public"."chat_messages"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
 ALTER TABLE "rag_trace_chunks" ADD CONSTRAINT "rag_trace_chunks_trace_id_rag_traces_id_fk"
  FOREIGN KEY ("trace_id") REFERENCES "public"."rag_traces"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
 ALTER TABLE "rag_evaluations" ADD CONSTRAINT "rag_evaluations_trace_id_rag_traces_id_fk"
  FOREIGN KEY ("trace_id") REFERENCES "public"."rag_traces"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION
 WHEN duplicate_object THEN null;
END $$;

CREATE INDEX IF NOT EXISTS "rag_traces_chat_created_at_index"
  ON "rag_traces" ("chat_id", "created_at");

CREATE INDEX IF NOT EXISTS "rag_traces_user_created_at_index"
  ON "rag_traces" ("user_id", "created_at");

CREATE INDEX IF NOT EXISTS "rag_traces_assistant_message_id_index"
  ON "rag_traces" ("assistant_message_id");

CREATE INDEX IF NOT EXISTS "rag_trace_chunks_trace_rank_index"
  ON "rag_trace_chunks" ("trace_id", "rank");

CREATE UNIQUE INDEX IF NOT EXISTS "rag_evaluations_trace_id_unique"
  ON "rag_evaluations" ("trace_id");
