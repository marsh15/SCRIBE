-- Scribe 2.0 T08 (Design B2, ADR-0011): source versions, profiles, index generations,
-- chunks, retrieval/evidence/eval tables, and the files / ingestion_jobs extensions.
-- Hand-written and idempotent: safe to run more than once, and safe on top of a
-- database built from lib/db-schema.ts via scripts/apply-migrations.sh (everything
-- is IF NOT EXISTS / duplicate-tolerant). RLS is intentionally NOT part of this
-- migration (task T15). Profile seeds live in 0011.

-- pgvector is required (vector columns + HNSW). Fail loudly and early without it.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    BEGIN
      CREATE EXTENSION vector;
    EXCEPTION WHEN OTHERS THEN
      RAISE EXCEPTION 'T08 migration requires the pgvector extension (CREATE EXTENSION vector failed: %). Enable pgvector on this database and re-run.', SQLERRM;
    END;
  END IF;
END $$;


-- ---------------------------------------------------------------- tables

CREATE TABLE IF NOT EXISTS "source_versions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_id" integer NOT NULL,
	"workspace_id" uuid NOT NULL,
	"version_number" integer NOT NULL,
	"storage_key" text,
	"sha256" text,
	"size_bytes" bigint,
	"mime_type" text,
	"upload_state" text DEFAULT 'uploading' NOT NULL,
	"duplicate_of" uuid,
	"created_by" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "source_versions_id_source_id_unique" UNIQUE("id","source_id"),
	CONSTRAINT "source_versions_version_number_check" CHECK ("source_versions"."version_number" > 0),
	CONSTRAINT "source_versions_upload_state_check" CHECK ("source_versions"."upload_state" IN ('uploading', 'stored', 'failed'))
);

CREATE TABLE IF NOT EXISTS "embedding_profiles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"provider" text NOT NULL,
	"model" text NOT NULL,
	"dimensions" integer NOT NULL,
	"normalized" boolean DEFAULT true NOT NULL,
	"config" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"config_hash" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "chunk_profiles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"config" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"config_hash" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "index_profiles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"parser_profile" text NOT NULL,
	"chunk_profile_id" uuid NOT NULL,
	"embedding_profile_id" uuid NOT NULL,
	"config" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "retrieval_profiles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"config" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"config_hash" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "index_generations" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_id" integer NOT NULL,
	"source_version_id" uuid NOT NULL,
	"index_profile_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"status" text DEFAULT 'BUILDING' NOT NULL,
	"expected_chunks" integer DEFAULT 0 NOT NULL,
	"embedded_chunks" integer DEFAULT 0 NOT NULL,
	"error_code" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"ready_at" timestamp with time zone,
	"activated_at" timestamp with time zone,
	"superseded_at" timestamp with time zone,
	CONSTRAINT "index_generations_id_source_profile_unique" UNIQUE("id","source_id","index_profile_id"),
	CONSTRAINT "index_generations_status_check" CHECK ("index_generations"."status" IN ('BUILDING','READY','ACTIVE','SUPERSEDED','FAILED','CANCELLED')),
	CONSTRAINT "index_generations_counts_check" CHECK ("index_generations"."expected_chunks" >= 0 AND "index_generations"."embedded_chunks" >= 0)
);

CREATE TABLE IF NOT EXISTS "active_indexes" (
	"source_id" integer NOT NULL,
	"index_profile_id" uuid NOT NULL,
	"generation_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"activated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "active_indexes_source_id_index_profile_id_pk" PRIMARY KEY("source_id","index_profile_id")
);

CREATE TABLE IF NOT EXISTS "document_blocks" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_version_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"block_index" integer NOT NULL,
	"type" text NOT NULL,
	"text" text NOT NULL,
	"heading_path" text[],
	"page" integer,
	"bbox" real[],
	"ocr" boolean DEFAULT false NOT NULL,
	"row_number" integer,
	"char_start" integer,
	"char_end" integer,
	"meta" jsonb,
	CONSTRAINT "document_blocks_bbox_check" CHECK ("document_blocks"."bbox" IS NULL OR cardinality("document_blocks"."bbox") = 4)
);

CREATE TABLE IF NOT EXISTS "chunks" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"generation_id" uuid NOT NULL,
	"source_version_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"parent_id" uuid,
	"chunk_index" integer NOT NULL,
	"content" text NOT NULL,
	"heading_text" text,
	"content_hash" text NOT NULL,
	"token_count" integer,
	"fts_en" "tsvector" GENERATED ALWAYS AS (setweight(to_tsvector('english', coalesce(heading_text,'')),'A') || setweight(to_tsvector('english', content),'B')) STORED,
	"fts_simple" "tsvector" GENERATED ALWAYS AS (to_tsvector('simple', coalesce(heading_text,'') || ' ' || content)) STORED
);

CREATE TABLE IF NOT EXISTS "chunk_source_spans" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"chunk_id" uuid NOT NULL,
	"block_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"char_start" integer,
	"char_end" integer,
	"page" integer,
	"bbox" real[]
);

CREATE TABLE IF NOT EXISTS "chunk_embeddings_1536" (
	"chunk_id" uuid PRIMARY KEY NOT NULL,
	"workspace_id" uuid NOT NULL,
	"embedding_profile_id" uuid NOT NULL,
	"embedding" vector(1536) NOT NULL
);

CREATE TABLE IF NOT EXISTS "chunk_embeddings_3072" (
	"chunk_id" uuid PRIMARY KEY NOT NULL,
	"workspace_id" uuid NOT NULL,
	"embedding_profile_id" uuid NOT NULL,
	"embedding" vector(3072) NOT NULL
);

CREATE TABLE IF NOT EXISTS "retrieval_traces" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"workspace_id" uuid NOT NULL,
	"chat_id" text,
	"message_id" text,
	"query" text NOT NULL,
	"profile_id" uuid,
	"config_hash" text,
	"status" text DEFAULT 'started' NOT NULL,
	"degraded" text,
	"stage_ms" jsonb,
	"counts" jsonb,
	"cost" jsonb,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "retrieval_candidates" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"trace_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"chunk_id" uuid,
	"dense_rank" integer,
	"dense_score" double precision,
	"lexical_rank" integer,
	"lexical_score" double precision,
	"fusion_rank" integer,
	"rerank_rank" integer,
	"rerank_score" double precision,
	"selected" boolean DEFAULT false NOT NULL
);

CREATE TABLE IF NOT EXISTS "evidence_bundles" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"trace_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"answerability" text NOT NULL,
	"top_rerank_score" double precision,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "evidence_items" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"bundle_id" uuid NOT NULL,
	"workspace_id" uuid NOT NULL,
	"label" text NOT NULL,
	"chunk_id" uuid,
	"source_version_id" uuid,
	"quote" text NOT NULL,
	"quote_hash" text NOT NULL,
	"page_start" integer,
	"page_end" integer,
	"heading_path" text[]
);

CREATE TABLE IF NOT EXISTS "claim_verifications" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"workspace_id" uuid NOT NULL,
	"message_id" text,
	"bundle_ids" uuid[],
	"claim_text" text NOT NULL,
	"labels" text[],
	"deterministic_ok" boolean,
	"status" text NOT NULL,
	"verifier_model" text,
	"prompt_version" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "eval_runs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"dataset_version" text NOT NULL,
	"split" text NOT NULL,
	"profile_id" uuid,
	"config_hash" text,
	"code_commit" text,
	"meta" jsonb,
	"started_at" timestamp with time zone DEFAULT now() NOT NULL,
	"finished_at" timestamp with time zone
);

CREATE TABLE IF NOT EXISTS "eval_results" (
	"run_id" uuid NOT NULL,
	"case_id" text NOT NULL,
	"metrics" jsonb,
	"ranking" jsonb,
	"latency_ms" integer,
	"cost" jsonb,
	CONSTRAINT "eval_results_run_id_case_id_pk" PRIMARY KEY("run_id","case_id")
);

-- ------------------------------------------------- files / ingestion_jobs columns
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "deleted_at" timestamp with time zone;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "desired_version_id" uuid;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "active_version_id" uuid;

ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "source_version_id" uuid;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "generation_id" uuid;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "lease_token" uuid;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "lease_expires_at" timestamp with time zone;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "error_code" text;
-- attempts, last_error, next_retry_at already exist (0003); keep these no-ops for safety.
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "attempts" integer DEFAULT 0 NOT NULL;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "last_error" text;
ALTER TABLE "ingestion_jobs" ADD COLUMN IF NOT EXISTS "next_retry_at" timestamp;

-- ---------------------------------------------------------------- indexes
-- (before foreign keys: composite FKs need their unique indexes to exist)

CREATE INDEX IF NOT EXISTS "active_indexes_workspace_id_index" ON "active_indexes" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "active_indexes_generation_id_index" ON "active_indexes" USING btree ("generation_id");

CREATE INDEX IF NOT EXISTS "chunk_embeddings_1536_workspace_id_index" ON "chunk_embeddings_1536" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "chunk_embeddings_1536_hnsw" ON "chunk_embeddings_1536" USING hnsw ("embedding" vector_cosine_ops) WITH (m=16,ef_construction=64);

CREATE INDEX IF NOT EXISTS "chunk_embeddings_3072_workspace_id_index" ON "chunk_embeddings_3072" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "chunk_profiles_name_unique" ON "chunk_profiles" USING btree ("name");

CREATE INDEX IF NOT EXISTS "chunk_source_spans_chunk_id_index" ON "chunk_source_spans" USING btree ("chunk_id");

CREATE INDEX IF NOT EXISTS "chunk_source_spans_block_id_index" ON "chunk_source_spans" USING btree ("block_id");

CREATE INDEX IF NOT EXISTS "chunk_source_spans_workspace_id_index" ON "chunk_source_spans" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "chunks_generation_id_chunk_index_unique" ON "chunks" USING btree ("generation_id","chunk_index");

CREATE INDEX IF NOT EXISTS "chunks_workspace_id_index" ON "chunks" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "chunks_generation_id_index" ON "chunks" USING btree ("generation_id");

CREATE INDEX IF NOT EXISTS "chunks_source_version_id_index" ON "chunks" USING btree ("source_version_id");

CREATE INDEX IF NOT EXISTS "chunks_parent_id_index" ON "chunks" USING btree ("parent_id");

CREATE INDEX IF NOT EXISTS "chunks_content_hash_index" ON "chunks" USING btree ("content_hash");

CREATE INDEX IF NOT EXISTS "chunks_fts_en_gin" ON "chunks" USING gin ("fts_en");

CREATE INDEX IF NOT EXISTS "chunks_fts_simple_gin" ON "chunks" USING gin ("fts_simple");

CREATE INDEX IF NOT EXISTS "claim_verifications_message_id_index" ON "claim_verifications" USING btree ("message_id");

CREATE INDEX IF NOT EXISTS "claim_verifications_workspace_id_index" ON "claim_verifications" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "document_blocks_source_version_id_block_index_unique" ON "document_blocks" USING btree ("source_version_id","block_index");

CREATE INDEX IF NOT EXISTS "document_blocks_workspace_id_index" ON "document_blocks" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "embedding_profiles_name_unique" ON "embedding_profiles" USING btree ("name");

CREATE INDEX IF NOT EXISTS "evidence_bundles_trace_id_index" ON "evidence_bundles" USING btree ("trace_id");

CREATE INDEX IF NOT EXISTS "evidence_bundles_workspace_id_index" ON "evidence_bundles" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "evidence_items_bundle_id_label_unique" ON "evidence_items" USING btree ("bundle_id","label");

CREATE INDEX IF NOT EXISTS "evidence_items_workspace_id_index" ON "evidence_items" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "evidence_items_chunk_id_index" ON "evidence_items" USING btree ("chunk_id");

CREATE UNIQUE INDEX IF NOT EXISTS "index_generations_version_profile_unique" ON "index_generations" USING btree ("source_version_id","index_profile_id");

CREATE UNIQUE INDEX IF NOT EXISTS "index_generations_one_active_unique" ON "index_generations" USING btree ("source_id","index_profile_id") WHERE "index_generations"."status" = 'ACTIVE';

CREATE INDEX IF NOT EXISTS "index_generations_workspace_id_index" ON "index_generations" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "index_generations_status_index" ON "index_generations" USING btree ("status");

CREATE UNIQUE INDEX IF NOT EXISTS "index_profiles_name_unique" ON "index_profiles" USING btree ("name");

CREATE INDEX IF NOT EXISTS "retrieval_candidates_trace_id_index" ON "retrieval_candidates" USING btree ("trace_id");

CREATE INDEX IF NOT EXISTS "retrieval_candidates_chunk_id_index" ON "retrieval_candidates" USING btree ("chunk_id");

CREATE INDEX IF NOT EXISTS "retrieval_candidates_workspace_id_index" ON "retrieval_candidates" USING btree ("workspace_id");

CREATE UNIQUE INDEX IF NOT EXISTS "retrieval_profiles_name_unique" ON "retrieval_profiles" USING btree ("name");

CREATE INDEX IF NOT EXISTS "retrieval_traces_workspace_created_at_index" ON "retrieval_traces" USING btree ("workspace_id","created_at");

CREATE INDEX IF NOT EXISTS "retrieval_traces_chat_id_index" ON "retrieval_traces" USING btree ("chat_id");

CREATE INDEX IF NOT EXISTS "retrieval_traces_message_id_index" ON "retrieval_traces" USING btree ("message_id");

CREATE UNIQUE INDEX IF NOT EXISTS "source_versions_source_id_version_number_unique" ON "source_versions" USING btree ("source_id","version_number");

CREATE INDEX IF NOT EXISTS "source_versions_workspace_id_index" ON "source_versions" USING btree ("workspace_id");

CREATE INDEX IF NOT EXISTS "source_versions_source_id_sha256_index" ON "source_versions" USING btree ("source_id","sha256");

-- ------------------------------------------------------------------ foreign keys

DO $$ BEGIN
  ALTER TABLE "active_indexes" ADD CONSTRAINT "active_indexes_source_id_files_id_fk" FOREIGN KEY ("source_id") REFERENCES "files"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "active_indexes" ADD CONSTRAINT "active_indexes_index_profile_id_index_profiles_id_fk" FOREIGN KEY ("index_profile_id") REFERENCES "index_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "active_indexes" ADD CONSTRAINT "active_indexes_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "active_indexes" ADD CONSTRAINT "active_indexes_generation_fk" FOREIGN KEY ("generation_id","source_id","index_profile_id") REFERENCES "index_generations"("id","source_id","index_profile_id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_1536" ADD CONSTRAINT "chunk_embeddings_1536_chunk_id_chunks_id_fk" FOREIGN KEY ("chunk_id") REFERENCES "chunks"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_1536" ADD CONSTRAINT "chunk_embeddings_1536_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_1536" ADD CONSTRAINT "chunk_embeddings_1536_embedding_profile_id_embedding_profiles_id_fk" FOREIGN KEY ("embedding_profile_id") REFERENCES "embedding_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_3072" ADD CONSTRAINT "chunk_embeddings_3072_chunk_id_chunks_id_fk" FOREIGN KEY ("chunk_id") REFERENCES "chunks"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_3072" ADD CONSTRAINT "chunk_embeddings_3072_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_embeddings_3072" ADD CONSTRAINT "chunk_embeddings_3072_embedding_profile_id_embedding_profiles_id_fk" FOREIGN KEY ("embedding_profile_id") REFERENCES "embedding_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_source_spans" ADD CONSTRAINT "chunk_source_spans_chunk_id_chunks_id_fk" FOREIGN KEY ("chunk_id") REFERENCES "chunks"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_source_spans" ADD CONSTRAINT "chunk_source_spans_block_id_document_blocks_id_fk" FOREIGN KEY ("block_id") REFERENCES "document_blocks"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunk_source_spans" ADD CONSTRAINT "chunk_source_spans_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunks" ADD CONSTRAINT "chunks_generation_id_index_generations_id_fk" FOREIGN KEY ("generation_id") REFERENCES "index_generations"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunks" ADD CONSTRAINT "chunks_source_version_id_source_versions_id_fk" FOREIGN KEY ("source_version_id") REFERENCES "source_versions"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunks" ADD CONSTRAINT "chunks_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "chunks" ADD CONSTRAINT "chunks_parent_id_chunks_id_fk" FOREIGN KEY ("parent_id") REFERENCES "chunks"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "claim_verifications" ADD CONSTRAINT "claim_verifications_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "claim_verifications" ADD CONSTRAINT "claim_verifications_message_id_chat_messages_id_fk" FOREIGN KEY ("message_id") REFERENCES "chat_messages"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "document_blocks" ADD CONSTRAINT "document_blocks_source_version_id_source_versions_id_fk" FOREIGN KEY ("source_version_id") REFERENCES "source_versions"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "document_blocks" ADD CONSTRAINT "document_blocks_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "eval_results" ADD CONSTRAINT "eval_results_run_id_eval_runs_id_fk" FOREIGN KEY ("run_id") REFERENCES "eval_runs"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "eval_runs" ADD CONSTRAINT "eval_runs_profile_id_retrieval_profiles_id_fk" FOREIGN KEY ("profile_id") REFERENCES "retrieval_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_bundles" ADD CONSTRAINT "evidence_bundles_trace_id_retrieval_traces_id_fk" FOREIGN KEY ("trace_id") REFERENCES "retrieval_traces"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_bundles" ADD CONSTRAINT "evidence_bundles_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_items" ADD CONSTRAINT "evidence_items_bundle_id_evidence_bundles_id_fk" FOREIGN KEY ("bundle_id") REFERENCES "evidence_bundles"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_items" ADD CONSTRAINT "evidence_items_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_items" ADD CONSTRAINT "evidence_items_chunk_id_chunks_id_fk" FOREIGN KEY ("chunk_id") REFERENCES "chunks"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "evidence_items" ADD CONSTRAINT "evidence_items_source_version_id_source_versions_id_fk" FOREIGN KEY ("source_version_id") REFERENCES "source_versions"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "files" ADD CONSTRAINT "files_desired_version_id_source_versions_id_fk" FOREIGN KEY ("desired_version_id") REFERENCES "source_versions"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "files" ADD CONSTRAINT "files_active_version_id_source_versions_id_fk" FOREIGN KEY ("active_version_id") REFERENCES "source_versions"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_generations" ADD CONSTRAINT "index_generations_source_id_files_id_fk" FOREIGN KEY ("source_id") REFERENCES "files"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_generations" ADD CONSTRAINT "index_generations_index_profile_id_index_profiles_id_fk" FOREIGN KEY ("index_profile_id") REFERENCES "index_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_generations" ADD CONSTRAINT "index_generations_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_generations" ADD CONSTRAINT "index_generations_source_version_source_fk" FOREIGN KEY ("source_version_id","source_id") REFERENCES "source_versions"("id","source_id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_profiles" ADD CONSTRAINT "index_profiles_chunk_profile_id_chunk_profiles_id_fk" FOREIGN KEY ("chunk_profile_id") REFERENCES "chunk_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "index_profiles" ADD CONSTRAINT "index_profiles_embedding_profile_id_embedding_profiles_id_fk" FOREIGN KEY ("embedding_profile_id") REFERENCES "embedding_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ingestion_jobs" ADD CONSTRAINT "ingestion_jobs_file_id_files_id_fk" FOREIGN KEY ("file_id") REFERENCES "files"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ingestion_jobs" ADD CONSTRAINT "ingestion_jobs_source_version_id_source_versions_id_fk" FOREIGN KEY ("source_version_id") REFERENCES "source_versions"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "ingestion_jobs" ADD CONSTRAINT "ingestion_jobs_generation_id_index_generations_id_fk" FOREIGN KEY ("generation_id") REFERENCES "index_generations"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_candidates" ADD CONSTRAINT "retrieval_candidates_trace_id_retrieval_traces_id_fk" FOREIGN KEY ("trace_id") REFERENCES "retrieval_traces"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_candidates" ADD CONSTRAINT "retrieval_candidates_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_candidates" ADD CONSTRAINT "retrieval_candidates_chunk_id_chunks_id_fk" FOREIGN KEY ("chunk_id") REFERENCES "chunks"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_traces" ADD CONSTRAINT "retrieval_traces_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_traces" ADD CONSTRAINT "retrieval_traces_chat_id_chats_id_fk" FOREIGN KEY ("chat_id") REFERENCES "chats"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_traces" ADD CONSTRAINT "retrieval_traces_message_id_chat_messages_id_fk" FOREIGN KEY ("message_id") REFERENCES "chat_messages"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "retrieval_traces" ADD CONSTRAINT "retrieval_traces_profile_id_retrieval_profiles_id_fk" FOREIGN KEY ("profile_id") REFERENCES "retrieval_profiles"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "source_versions" ADD CONSTRAINT "source_versions_source_id_files_id_fk" FOREIGN KEY ("source_id") REFERENCES "files"("id") ON DELETE cascade ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "source_versions" ADD CONSTRAINT "source_versions_workspace_id_workspaces_id_fk" FOREIGN KEY ("workspace_id") REFERENCES "workspaces"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE "source_versions" ADD CONSTRAINT "source_versions_duplicate_of_source_versions_id_fk" FOREIGN KEY ("duplicate_of") REFERENCES "source_versions"("id") ON DELETE set null ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ------------------------------------------------------------ immutability triggers
-- Profiles are immutable: a change creates a new row (Design B2).
CREATE OR REPLACE FUNCTION scribe_reject_profile_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION '% rows are immutable; insert a new profile instead of updating %', TG_TABLE_NAME, OLD.id
    USING ERRCODE = 'restrict_violation';
END;
$fn$;

DROP TRIGGER IF EXISTS "embedding_profiles_immutable" ON "embedding_profiles";
CREATE TRIGGER "embedding_profiles_immutable" BEFORE UPDATE ON "embedding_profiles"
  FOR EACH ROW EXECUTE FUNCTION scribe_reject_profile_update();
DROP TRIGGER IF EXISTS "chunk_profiles_immutable" ON "chunk_profiles";
CREATE TRIGGER "chunk_profiles_immutable" BEFORE UPDATE ON "chunk_profiles"
  FOR EACH ROW EXECUTE FUNCTION scribe_reject_profile_update();
DROP TRIGGER IF EXISTS "index_profiles_immutable" ON "index_profiles";
CREATE TRIGGER "index_profiles_immutable" BEFORE UPDATE ON "index_profiles"
  FOR EACH ROW EXECUTE FUNCTION scribe_reject_profile_update();
DROP TRIGGER IF EXISTS "retrieval_profiles_immutable" ON "retrieval_profiles";
CREATE TRIGGER "retrieval_profiles_immutable" BEFORE UPDATE ON "retrieval_profiles"
  FOR EACH ROW EXECUTE FUNCTION scribe_reject_profile_update();

-- INV-6: a stored source version never changes. The only columns that may change
-- after insert are duplicate_of (set when identical bytes are detected) and
-- upload_state (uploading -> stored | failed). Everything else, including
-- storage_key, sha256, size_bytes, mime_type, created_*, is frozen.
CREATE OR REPLACE FUNCTION scribe_guard_source_version_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.source_id IS DISTINCT FROM OLD.source_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
     OR NEW.version_number IS DISTINCT FROM OLD.version_number
     OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
     OR NEW.sha256 IS DISTINCT FROM OLD.sha256
     OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
     OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
     OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'source_versions row % is immutable (only duplicate_of and upload_state may change)', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  RETURN NEW;
END;
$fn$;

DROP TRIGGER IF EXISTS "source_versions_immutable" ON "source_versions";
CREATE TRIGGER "source_versions_immutable" BEFORE UPDATE ON "source_versions"
  FOR EACH ROW EXECUTE FUNCTION scribe_guard_source_version_update();
