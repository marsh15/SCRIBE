import { sql } from "drizzle-orm";
import {
  pgTable,
  text,
  vector,
  serial,
  timestamp,
  integer,
  jsonb,
  boolean,
  bigint,
  doublePrecision,
  index,
  uniqueIndex,
  uuid,
  primaryKey,
  customType,
  check,
  unique,
  foreignKey,
  real,
  type AnyPgColumn,
} from "drizzle-orm/pg-core";

export const SOURCE_STATUSES = [
  "uploading",
  "queued",
  "processing",
  "retrying",
  "ready",
  "failed",
] as const;

export type SourceStatus = (typeof SOURCE_STATUSES)[number];

export const WORKSPACE_KINDS = ["personal", "eval"] as const;
export type WorkspaceKind = (typeof WORKSPACE_KINDS)[number];

export const workspaces = pgTable(
  "workspaces",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    kind: text("kind", { enum: WORKSPACE_KINDS }).notNull(),
    ownerUserId: text("owner_user_id"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
  },
  (table) => ({
    // At most one personal workspace per user (partial unique index).
    personalOwnerUnique: uniqueIndex("workspaces_personal_owner_unique")
      .on(table.ownerUserId)
      .where(sql`${table.kind} = 'personal'`),
  })
);

export const workspaceMembers = pgTable(
  "workspace_members",
  {
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id, { onDelete: "cascade" })
      .notNull(),
    userId: text("user_id").notNull(),
    role: text("role").default("owner").notNull(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.workspaceId, table.userId] }),
    userIndex: index("workspace_members_user_id_index").on(table.userId),
  })
);

export const files = pgTable(
  "files",
  {
    id: serial("id").primaryKey(),
    name: text("name").notNull(),
    type: text("type").notNull(),
    size: integer("size").notNull(),
    userId: text("user_id").notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    fileData: text("file_data"), // legacy base64-encoded original
    extractedText: text("extracted_text"),
    storageKey: text("storage_key"),
    storageUrl: text("storage_url"),
    status: text("status", { enum: SOURCE_STATUSES }).default("ready").notNull(),
    processingError: text("processing_error"),
    textBytes: integer("text_bytes"),
    // Scribe 2.0 (T08): soft delete + version pointers. FKs to source_versions
    // are added in migration 0010 after that table exists.
    deletedAt: timestamp("deleted_at", { withTimezone: true }),
    // T20 (0016): retry state of the background purge of a soft-deleted Source.
    purgeAttempts: integer("purge_attempts").default(0).notNull(),
    purgeNextRetryAt: timestamp("purge_next_retry_at", { withTimezone: true }),
    purgeErrorCode: text("purge_error_code"),
    purgeLastError: text("purge_last_error"),
    // T22 (0018): legacy Source whose original blob is missing/unreadable keeps its legacy generation and shows
    // "Re-upload to upgrade" until a new version is uploaded.
    reuploadRequiredAt: timestamp("reupload_required_at", { withTimezone: true }),
    reuploadReason: text("reupload_reason"),
    desiredVersionId: uuid("desired_version_id").references(
      (): AnyPgColumn => sourceVersions.id,
      { onDelete: "set null" }
    ),
    activeVersionId: uuid("active_version_id").references(
      (): AnyPgColumn => sourceVersions.id,
      { onDelete: "set null" }
    ),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => ({
    statusUpdatedIndex: index("files_status_updated_at_index").on(
      table.status,
      table.updatedAt
    ),
    workspaceIndex: index("files_workspace_id_index").on(table.workspaceId),
  })
);

export const documents = pgTable(
  "documents",
  {
    id: serial("id").primaryKey(),
    fileId: integer("file_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    chunkIndex: integer("chunk_index").notNull(),
    content: text("content").notNull(),
    metadata: jsonb("metadata"),
    embeddings: vector("embeddings", { dimensions: 3072 }).notNull(),
  },
  (table) => ({
    fileChunkUnique: uniqueIndex("documents_file_id_chunk_index_unique").on(
      table.fileId,
      table.chunkIndex
    ),
  })
);

export const chats = pgTable("chats", {
  id: text("id").primaryKey(),
  title: text("title").notNull(),
  userId: text("user_id").notNull(),
  workspaceId: uuid("workspace_id")
    .references(() => workspaces.id)
    .notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
}, (table) => ({
  workspaceIndex: index("chats_workspace_id_index").on(table.workspaceId),
}));

export const chatMessages = pgTable("chat_messages", {
  id: text("id").primaryKey(),
  chatId: text("chat_id")
    .references(() => chats.id, { onDelete: "cascade" })
    .notNull(),
  role: text("role").notNull(),
  content: text("content").notNull(),
  parts: jsonb("parts"),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export const ragTraces = pgTable(
  "rag_traces",
  {
    id: text("id").primaryKey(),
    chatId: text("chat_id")
      .references(() => chats.id, { onDelete: "cascade" })
      .notNull(),
    userId: text("user_id").notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    userMessageId: text("user_message_id").references(() => chatMessages.id, {
      onDelete: "set null",
    }),
    assistantMessageId: text("assistant_message_id").references(
      () => chatMessages.id,
      { onDelete: "set null" }
    ),
    query: text("query").notNull(),
    status: text("status").default("retrieved").notNull(),
    embeddingMs: integer("embedding_ms").default(0).notNull(),
    retrievalMs: integer("retrieval_ms").default(0).notNull(),
    generationMs: integer("generation_ms"),
    totalMs: integer("total_ms"),
    topK: integer("top_k").notNull(),
    threshold: doublePrecision("threshold").notNull(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
  },
  (table) => ({
    chatCreatedIndex: index("rag_traces_chat_created_at_index").on(
      table.chatId,
      table.createdAt
    ),
    userCreatedIndex: index("rag_traces_user_created_at_index").on(
      table.userId,
      table.createdAt
    ),
    assistantMessageIndex: index("rag_traces_assistant_message_id_index").on(
      table.assistantMessageId
    ),
    workspaceIndex: index("rag_traces_workspace_id_index").on(table.workspaceId),
  })
);

export const ragTraceChunks = pgTable(
  "rag_trace_chunks",
  {
    id: serial("id").primaryKey(),
    traceId: text("trace_id")
      .references(() => ragTraces.id, { onDelete: "cascade" })
      .notNull(),
    documentId: integer("document_id").notNull(),
    fileId: integer("file_id").notNull(),
    fileName: text("file_name").notNull(),
    chunkIndex: integer("chunk_index").notNull(),
    similarity: doublePrecision("similarity").notNull(),
    rank: integer("rank").notNull(),
    contentPreview: text("content_preview").notNull(),
    metadata: jsonb("metadata"),
  },
  (table) => ({
    traceRankIndex: index("rag_trace_chunks_trace_rank_index").on(
      table.traceId,
      table.rank
    ),
  })
);

export const ragEvaluations = pgTable(
  "rag_evaluations",
  {
    id: serial("id").primaryKey(),
    traceId: text("trace_id")
      .references(() => ragTraces.id, { onDelete: "cascade" })
      .notNull(),
    status: text("status").default("completed").notNull(),
    judgeModel: text("judge_model").notNull(),
    groundednessScore: doublePrecision("groundedness_score"),
    answerRelevanceScore: doublePrecision("answer_relevance_score"),
    citationSupportScore: doublePrecision("citation_support_score"),
    overallScore: doublePrecision("overall_score"),
    verdict: text("verdict"),
    rationale: text("rationale"),
    error: text("error"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
  },
  (table) => ({
    traceUnique: uniqueIndex("rag_evaluations_trace_id_unique").on(table.traceId),
  })
);

export const billingCustomers = pgTable(
  "billing_customers",
  {
    id: serial("id").primaryKey(),
    userId: text("user_id").notNull(),
    razorpayCustomerId: text("razorpay_customer_id"),
    defaultGateway: text("default_gateway"), // razorpay
    billingCountry: text("billing_country"),
    currency: text("currency").default("INR").notNull(),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => ({
    userUnique: uniqueIndex("billing_customers_user_id_unique").on(table.userId),
  })
);

export const subscriptions = pgTable("subscriptions", {
  id: serial("id").primaryKey(),
  userId: text("user_id").notNull(),
  planCode: text("plan_code").notNull(), // free | pro | team
  gateway: text("gateway").notNull(), // razorpay
  providerSubscriptionId: text("provider_subscription_id"),
  status: text("status").default("inactive").notNull(), // active | trialing | past_due | canceled | inactive
  currentPeriodStart: timestamp("current_period_start"),
  currentPeriodEnd: timestamp("current_period_end"),
  cancelAtPeriodEnd: boolean("cancel_at_period_end").default(false).notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export const usageEvents = pgTable("usage_events", {
  id: serial("id").primaryKey(),
  userId: text("user_id").notNull(),
  metric: text("metric").notNull(), // model_input_tokens | model_output_tokens | embedding_input_tokens | storage_gb_day
  quantity: bigint("quantity", { mode: "number" }).notNull(),
  unit: text("unit").notNull(), // tokens | gb_day
  sourceType: text("source_type"), // chat | upload | ingest | storage_rollup
  sourceId: text("source_id"),
  idempotencyKey: text("idempotency_key").unique(),
  isEstimated: boolean("is_estimated").default(false).notNull(),
  occurredAt: timestamp("occurred_at").defaultNow().notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
});

export const billingCycles = pgTable("billing_cycles", {
  id: serial("id").primaryKey(),
  userId: text("user_id").notNull(),
  periodStart: timestamp("period_start").notNull(),
  periodEnd: timestamp("period_end").notNull(),
  includedInr: doublePrecision("included_inr").default(0).notNull(),
  consumedInr: doublePrecision("consumed_inr").default(0).notNull(),
  overageInr: doublePrecision("overage_inr").default(0).notNull(),
  settled: boolean("settled").default(false).notNull(),
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

export const paymentEvents = pgTable(
  "payment_events",
  {
    id: serial("id").primaryKey(),
    gateway: text("gateway").notNull(), // razorpay
    eventId: text("event_id").notNull(),
    eventType: text("event_type"),
    payload: jsonb("payload"),
    processed: boolean("processed").default(false).notNull(),
    processedAt: timestamp("processed_at"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
  },
  (table) => ({
    gatewayEventUnique: uniqueIndex("payment_events_gateway_event_unique").on(
      table.gateway,
      table.eventId
    ),
  })
);

export const ingestionJobs = pgTable(
  "ingestion_jobs",
  {
    id: serial("id").primaryKey(),
    fileId: integer("file_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    status: text("status").default("queued").notNull(),
    attempts: integer("attempts").default(0).notNull(),
    lastError: text("last_error"),
    nextRetryAt: timestamp("next_retry_at"),
    // Scribe 2.0 (T08): per-version jobs with lease fencing (Design B3).
    sourceVersionId: uuid("source_version_id").references(
      () => sourceVersions.id,
      { onDelete: "set null" }
    ),
    generationId: uuid("generation_id").references(() => indexGenerations.id, {
      onDelete: "set null",
    }),
    leaseToken: uuid("lease_token"),
    leaseExpiresAt: timestamp("lease_expires_at", { withTimezone: true }),
    errorCode: text("error_code"),
    // T18: PARSE | CHUNK | EMBED | DONE | FAILED | CANCELLED | SUPERSEDED
    stage: text("stage"),
    startedAt: timestamp("started_at"),
    finishedAt: timestamp("finished_at"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => ({
    // T18 (0014): one job per (file, version); jobs queued before the version exists dedupe on the file alone.
    fileVersionUnique: uniqueIndex("ingestion_jobs_file_version_unique").on(
      table.fileId,
      sql`COALESCE(${table.sourceVersionId}, '00000000-0000-0000-0000-000000000000'::uuid)`
    ),
    queueIndex: index("ingestion_jobs_queue_index").on(
      table.status,
      table.nextRetryAt,
      table.createdAt
    ),
  })
);

// ---------------------------------------------------------------------------
// Scribe 2.0 (T08): source versions, profiles, index generations, chunks,
// retrieval/evidence/eval tables. Design B2, ADR-0011. RLS is T15, not here.
// Hand-written idempotent SQL lives in drizzle/0010_*.sql and 0011_*.sql;
// triggers (immutability) exist only there because Drizzle cannot express them.
// ---------------------------------------------------------------------------

const tsvector = customType<{ data: string }>({
  dataType() {
    return "tsvector";
  },
});

const tstz = (name: string) => timestamp(name, { withTimezone: true });

export const UPLOAD_STATES = ["uploading", "stored", "failed"] as const;
export type UploadState = (typeof UPLOAD_STATES)[number];

export const GENERATION_STATUSES = [
  "BUILDING",
  "READY",
  "ACTIVE",
  "SUPERSEDED",
  "FAILED",
  "CANCELLED",
] as const;
export type GenerationStatus = (typeof GENERATION_STATUSES)[number];

export const sourceVersions = pgTable(
  "source_versions",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    sourceId: integer("source_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    versionNumber: integer("version_number").notNull(),
    storageKey: text("storage_key"),
    sha256: text("sha256"),
    sizeBytes: bigint("size_bytes", { mode: "number" }),
    mimeType: text("mime_type"),
    uploadState: text("upload_state", { enum: UPLOAD_STATES })
      .default("uploading")
      .notNull(),
    duplicateOf: uuid("duplicate_of").references(
      (): AnyPgColumn => sourceVersions.id,
      { onDelete: "set null" }
    ),
    createdBy: text("created_by"),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    versionUnique: uniqueIndex("source_versions_source_id_version_number_unique").on(
      table.sourceId,
      table.versionNumber
    ),
    // Target for composite FKs that must agree on the owning source.
    // (a UNIQUE constraint, not an index, so drizzle-kit emits it inside CREATE
    // TABLE and the FK ALTERs that follow can reference it.)
    idSourceUnique: unique("source_versions_id_source_id_unique").on(
      table.id,
      table.sourceId
    ),
    workspaceIndex: index("source_versions_workspace_id_index").on(table.workspaceId),
    sourceShaIndex: index("source_versions_source_id_sha256_index").on(
      table.sourceId,
      table.sha256
    ),
    versionPositive: check("source_versions_version_number_check", sql`${table.versionNumber} > 0`),
    uploadStateCheck: check(
      "source_versions_upload_state_check",
      sql`${table.uploadState} IN ('uploading', 'stored', 'failed')`
    ),
  })
);

export const embeddingProfiles = pgTable(
  "embedding_profiles",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    name: text("name").notNull(),
    provider: text("provider").notNull(),
    model: text("model").notNull(),
    dimensions: integer("dimensions").notNull(),
    normalized: boolean("normalized").default(true).notNull(),
    config: jsonb("config").default(sql`'{}'::jsonb`).notNull(),
    configHash: text("config_hash").notNull(),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    nameUnique: uniqueIndex("embedding_profiles_name_unique").on(table.name),
  })
);

export const chunkProfiles = pgTable(
  "chunk_profiles",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    name: text("name").notNull(),
    config: jsonb("config").default(sql`'{}'::jsonb`).notNull(),
    configHash: text("config_hash").notNull(),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    nameUnique: uniqueIndex("chunk_profiles_name_unique").on(table.name),
  })
);

export const indexProfiles = pgTable(
  "index_profiles",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    name: text("name").notNull(),
    parserProfile: text("parser_profile").notNull(),
    chunkProfileId: uuid("chunk_profile_id")
      .references(() => chunkProfiles.id)
      .notNull(),
    embeddingProfileId: uuid("embedding_profile_id")
      .references(() => embeddingProfiles.id)
      .notNull(),
    config: jsonb("config").default(sql`'{}'::jsonb`).notNull(),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    nameUnique: uniqueIndex("index_profiles_name_unique").on(table.name),
  })
);

export const retrievalProfiles = pgTable(
  "retrieval_profiles",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    name: text("name").notNull(),
    config: jsonb("config").default(sql`'{}'::jsonb`).notNull(),
    configHash: text("config_hash").notNull(),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    nameUnique: uniqueIndex("retrieval_profiles_name_unique").on(table.name),
  })
);

export const indexGenerations = pgTable(
  "index_generations",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    // source_id is denormalised from source_versions so "one ACTIVE generation
    // per (source, index profile)" can be a plain partial unique index.
    sourceId: integer("source_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    sourceVersionId: uuid("source_version_id").notNull(),
    indexProfileId: uuid("index_profile_id")
      .references(() => indexProfiles.id)
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    status: text("status", { enum: GENERATION_STATUSES }).default("BUILDING").notNull(),
    expectedChunks: integer("expected_chunks").default(0).notNull(),
    embeddedChunks: integer("embedded_chunks").default(0).notNull(),
    errorCode: text("error_code"),
    createdAt: tstz("created_at").defaultNow().notNull(),
    updatedAt: tstz("updated_at").defaultNow().notNull(),
    readyAt: tstz("ready_at"),
    activatedAt: tstz("activated_at"),
    supersededAt: tstz("superseded_at"),
  },
  (table) => ({
    versionFk: foreignKey({
      name: "index_generations_source_version_source_fk",
      columns: [table.sourceVersionId, table.sourceId],
      foreignColumns: [sourceVersions.id, sourceVersions.sourceId],
    }).onDelete("cascade"),
    statusCheck: check(
      "index_generations_status_check",
      sql`${table.status} IN ('BUILDING','READY','ACTIVE','SUPERSEDED','FAILED','CANCELLED')`
    ),
    countsCheck: check(
      "index_generations_counts_check",
      sql`${table.expectedChunks} >= 0 AND ${table.embeddedChunks} >= 0`
    ),
    versionProfileUnique: uniqueIndex("index_generations_version_profile_unique").on(
      table.sourceVersionId,
      table.indexProfileId
    ),
    // INV-4: at most one ACTIVE generation per (source, index profile).
    oneActiveUnique: uniqueIndex("index_generations_one_active_unique")
      .on(table.sourceId, table.indexProfileId)
      .where(sql`${table.status} = 'ACTIVE'`),
    // Target for active_indexes' composite FK.
    pointerTarget: unique("index_generations_id_source_profile_unique").on(
      table.id,
      table.sourceId,
      table.indexProfileId
    ),
    workspaceIndex: index("index_generations_workspace_id_index").on(table.workspaceId),
    statusIndex: index("index_generations_status_index").on(table.status),
  })
);

export const activeIndexes = pgTable(
  "active_indexes",
  {
    sourceId: integer("source_id")
      .references(() => files.id, { onDelete: "cascade" })
      .notNull(),
    indexProfileId: uuid("index_profile_id")
      .references(() => indexProfiles.id)
      .notNull(),
    generationId: uuid("generation_id").notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    activatedAt: tstz("activated_at").defaultNow().notNull(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.sourceId, table.indexProfileId] }),
    generationFk: foreignKey({
      name: "active_indexes_generation_fk",
      columns: [table.generationId, table.sourceId, table.indexProfileId],
      foreignColumns: [
        indexGenerations.id,
        indexGenerations.sourceId,
        indexGenerations.indexProfileId,
      ],
    }).onDelete("cascade"),
    workspaceIndex: index("active_indexes_workspace_id_index").on(table.workspaceId),
    generationIndex: index("active_indexes_generation_id_index").on(table.generationId),
  })
);

export const documentBlocks = pgTable(
  "document_blocks",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    sourceVersionId: uuid("source_version_id")
      .references(() => sourceVersions.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    blockIndex: integer("block_index").notNull(),
    type: text("type").notNull(),
    text: text("text").notNull(),
    headingPath: text("heading_path").array(),
    page: integer("page"),
    bbox: real("bbox").array(),
    ocr: boolean("ocr").default(false).notNull(),
    rowNumber: integer("row_number"),
    charStart: integer("char_start"),
    charEnd: integer("char_end"),
    meta: jsonb("meta"),
  },
  (table) => ({
    blockUnique: uniqueIndex("document_blocks_source_version_id_block_index_unique").on(
      table.sourceVersionId,
      table.blockIndex
    ),
    workspaceIndex: index("document_blocks_workspace_id_index").on(table.workspaceId),
    bboxCheck: check(
      "document_blocks_bbox_check",
      sql`${table.bbox} IS NULL OR cardinality(${table.bbox}) = 4`
    ),
  })
);

// T21 (0017): durable OCR progress per source version, one row per transcribed page ('' = blank page, still done).
// Untrusted document text (INV-13). Cascades with the version.
export const sourceVersionOcrPages = pgTable(
  "source_version_ocr_pages",
  {
    sourceVersionId: uuid("source_version_id")
      .references(() => sourceVersions.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    page: integer("page").notNull(),
    text: text("text").notNull(),
    model: text("model").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .defaultNow()
      .notNull(),
  },
  (table) => ({
    pk: primaryKey({
      name: "source_version_ocr_pages_pk",
      columns: [table.sourceVersionId, table.page],
    }),
    workspaceIndex: index("source_version_ocr_pages_workspace_id_index").on(
      table.workspaceId
    ),
    pageCheck: check("source_version_ocr_pages_page_check", sql`${table.page} >= 1`),
  })
);

export const chunks = pgTable(
  "chunks",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    generationId: uuid("generation_id")
      .references(() => indexGenerations.id, { onDelete: "cascade" })
      .notNull(),
    sourceVersionId: uuid("source_version_id")
      .references(() => sourceVersions.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    parentId: uuid("parent_id").references((): AnyPgColumn => chunks.id, {
      onDelete: "set null",
    }),
    chunkIndex: integer("chunk_index").notNull(),
    content: text("content").notNull(),
    headingText: text("heading_text"),
    contentHash: text("content_hash").notNull(),
    tokenCount: integer("token_count"),
    ftsEn: tsvector("fts_en").generatedAlwaysAs(
      sql`setweight(to_tsvector('english', coalesce(heading_text,'')),'A') || setweight(to_tsvector('english', content),'B')`
    ),
    ftsSimple: tsvector("fts_simple").generatedAlwaysAs(
      sql`to_tsvector('simple', coalesce(heading_text,'') || ' ' || content)`
    ),
  },
  (table) => ({
    // Parents and children share one chunk_index sequence per generation.
    chunkUnique: uniqueIndex("chunks_generation_id_chunk_index_unique").on(
      table.generationId,
      table.chunkIndex
    ),
    workspaceIndex: index("chunks_workspace_id_index").on(table.workspaceId),
    generationIndex: index("chunks_generation_id_index").on(table.generationId),
    sourceVersionIndex: index("chunks_source_version_id_index").on(table.sourceVersionId),
    parentIndex: index("chunks_parent_id_index").on(table.parentId),
    contentHashIndex: index("chunks_content_hash_index").on(table.contentHash),
    ftsEnIndex: index("chunks_fts_en_gin").using("gin", table.ftsEn),
    ftsSimpleIndex: index("chunks_fts_simple_gin").using("gin", table.ftsSimple),
  })
);

export const chunkSourceSpans = pgTable(
  "chunk_source_spans",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    chunkId: uuid("chunk_id")
      .references(() => chunks.id, { onDelete: "cascade" })
      .notNull(),
    blockId: uuid("block_id")
      .references(() => documentBlocks.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    charStart: integer("char_start"),
    charEnd: integer("char_end"),
    page: integer("page"),
    bbox: real("bbox").array(),
  },
  (table) => ({
    chunkIndex: index("chunk_source_spans_chunk_id_index").on(table.chunkId),
    blockIndex: index("chunk_source_spans_block_id_index").on(table.blockId),
    workspaceIndex: index("chunk_source_spans_workspace_id_index").on(table.workspaceId),
  })
);

export const chunkEmbeddings1536 = pgTable(
  "chunk_embeddings_1536",
  {
    chunkId: uuid("chunk_id")
      .references(() => chunks.id, { onDelete: "cascade" })
      .primaryKey(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    embeddingProfileId: uuid("embedding_profile_id")
      .references(() => embeddingProfiles.id)
      .notNull(),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
  },
  (table) => ({
    workspaceIndex: index("chunk_embeddings_1536_workspace_id_index").on(table.workspaceId),
    hnsw: index("chunk_embeddings_1536_hnsw")
      .using("hnsw", table.embedding.op("vector_cosine_ops"))
      .with({ m: 16, ef_construction: 64 }),
  })
);

// Legacy 3072-dim vectors: exact search only. pgvector HNSW/IVFFlat cap at 2000
// dimensions, so there is deliberately NO ANN index on this table.
export const chunkEmbeddings3072 = pgTable(
  "chunk_embeddings_3072",
  {
    chunkId: uuid("chunk_id")
      .references(() => chunks.id, { onDelete: "cascade" })
      .primaryKey(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    embeddingProfileId: uuid("embedding_profile_id")
      .references(() => embeddingProfiles.id)
      .notNull(),
    embedding: vector("embedding", { dimensions: 3072 }).notNull(),
  },
  (table) => ({
    workspaceIndex: index("chunk_embeddings_3072_workspace_id_index").on(table.workspaceId),
  })
);

export const retrievalTraces = pgTable(
  "retrieval_traces",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    chatId: text("chat_id").references(() => chats.id, { onDelete: "set null" }),
    messageId: text("message_id").references(() => chatMessages.id, {
      onDelete: "set null",
    }),
    query: text("query").notNull(),
    profileId: uuid("profile_id").references(() => retrievalProfiles.id),
    configHash: text("config_hash"),
    status: text("status").default("started").notNull(),
    degraded: text("degraded"),
    stageMs: jsonb("stage_ms"),
    counts: jsonb("counts"),
    cost: jsonb("cost"),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    workspaceCreatedIndex: index("retrieval_traces_workspace_created_at_index").on(
      table.workspaceId,
      table.createdAt
    ),
    chatIndex: index("retrieval_traces_chat_id_index").on(table.chatId),
    messageIndex: index("retrieval_traces_message_id_index").on(table.messageId),
  })
);

export const answerFeedback = pgTable(
  "answer_feedback",
  {
    id: serial("id").primaryKey(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id, { onDelete: "cascade" })
      .notNull(),
    chatId: text("chat_id")
      .references(() => chats.id, { onDelete: "cascade" })
      .notNull(),
    messageId: text("message_id")
      .references(() => chatMessages.id, { onDelete: "cascade" })
      .notNull(),
    ragTraceId: text("rag_trace_id").references(() => ragTraces.id, { onDelete: "set null" }),
    retrievalTraceId: uuid("retrieval_trace_id").references(() => retrievalTraces.id, { onDelete: "set null" }),
    userId: text("user_id").notNull(),
    rating: text("rating").notNull(),
    reasons: text("reasons").array().default(sql`'{}'::text[]`).notNull(),
    comment: text("comment"),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    workspaceCreatedIndex: index("answer_feedback_workspace_created_at_index").on(
      table.workspaceId,
      table.createdAt
    ),
    ratingCheck: check("answer_feedback_rating_check", sql`${table.rating} IN ('up', 'down')`),
    reasonsCheck: check(
      "answer_feedback_reasons_check",
      sql`${table.reasons} <@ ARRAY['wrong_citation', 'missing_info', 'outdated_version', 'should_abstain']::text[]`
    ),
    commentLengthCheck: check(
      "answer_feedback_comment_length_check",
      sql`${table.comment} IS NULL OR char_length(${table.comment}) <= 2000`
    ),
  })
);

export const retrievalCandidates = pgTable(
  "retrieval_candidates",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    traceId: uuid("trace_id")
      .references(() => retrievalTraces.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    // SET NULL: a purged chunk must not delete the historical trace row.
    chunkId: uuid("chunk_id").references(() => chunks.id, { onDelete: "set null" }),
    denseRank: integer("dense_rank"),
    denseScore: doublePrecision("dense_score"),
    lexicalRank: integer("lexical_rank"),
    lexicalScore: doublePrecision("lexical_score"),
    fusionRank: integer("fusion_rank"),
    rerankRank: integer("rerank_rank"),
    rerankScore: doublePrecision("rerank_score"),
    selected: boolean("selected").default(false).notNull(),
  },
  (table) => ({
    traceIndex: index("retrieval_candidates_trace_id_index").on(table.traceId),
    chunkIndex: index("retrieval_candidates_chunk_id_index").on(table.chunkId),
    workspaceIndex: index("retrieval_candidates_workspace_id_index").on(table.workspaceId),
  })
);

export const evidenceBundles = pgTable(
  "evidence_bundles",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    traceId: uuid("trace_id")
      .references(() => retrievalTraces.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    answerability: text("answerability").notNull(),
    topRerankScore: doublePrecision("top_rerank_score"),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    traceIndex: index("evidence_bundles_trace_id_index").on(table.traceId),
    workspaceIndex: index("evidence_bundles_workspace_id_index").on(table.workspaceId),
  })
);

export const evidenceItems = pgTable(
  "evidence_items",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    bundleId: uuid("bundle_id")
      .references(() => evidenceBundles.id, { onDelete: "cascade" })
      .notNull(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    label: text("label").notNull(),
    chunkId: uuid("chunk_id").references(() => chunks.id, { onDelete: "set null" }),
    sourceVersionId: uuid("source_version_id").references(() => sourceVersions.id, {
      onDelete: "set null",
    }),
    quote: text("quote").notNull(),
    quoteHash: text("quote_hash").notNull(),
    pageStart: integer("page_start"),
    pageEnd: integer("page_end"),
    headingPath: text("heading_path").array(),
  },
  (table) => ({
    bundleLabelUnique: uniqueIndex("evidence_items_bundle_id_label_unique").on(
      table.bundleId,
      table.label
    ),
    workspaceIndex: index("evidence_items_workspace_id_index").on(table.workspaceId),
    chunkIndex: index("evidence_items_chunk_id_index").on(table.chunkId),
  })
);

export const claimVerifications = pgTable(
  "claim_verifications",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    workspaceId: uuid("workspace_id")
      .references(() => workspaces.id)
      .notNull(),
    messageId: text("message_id").references(() => chatMessages.id, {
      onDelete: "cascade",
    }),
    bundleIds: uuid("bundle_ids").array(),
    claimText: text("claim_text").notNull(),
    labels: text("labels").array(),
    deterministicOk: boolean("deterministic_ok"),
    status: text("status").notNull(),
    verifierModel: text("verifier_model"),
    promptVersion: text("prompt_version"),
    createdAt: tstz("created_at").defaultNow().notNull(),
  },
  (table) => ({
    messageIndex: index("claim_verifications_message_id_index").on(table.messageId),
    workspaceIndex: index("claim_verifications_workspace_id_index").on(table.workspaceId),
  })
);

// Eval tables are global (not workspace-owned): runs measure profiles against
// datasets held in the eval workspace, so they carry no workspace_id.
export const evalRuns = pgTable("eval_runs", {
  id: uuid("id").defaultRandom().primaryKey(),
  datasetVersion: text("dataset_version").notNull(),
  split: text("split").notNull(),
  profileId: uuid("profile_id").references(() => retrievalProfiles.id),
  configHash: text("config_hash"),
  codeCommit: text("code_commit"),
  // INV-11: model ids, prompt versions, etc.
  meta: jsonb("meta"),
  startedAt: tstz("started_at").defaultNow().notNull(),
  finishedAt: tstz("finished_at"),
});

export const evalResults = pgTable(
  "eval_results",
  {
    runId: uuid("run_id")
      .references(() => evalRuns.id, { onDelete: "cascade" })
      .notNull(),
    caseId: text("case_id").notNull(),
    metrics: jsonb("metrics"),
    ranking: jsonb("ranking"),
    latencyMs: integer("latency_ms"),
    cost: jsonb("cost"),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.runId, table.caseId] }),
  })
);

export type InsertFile = typeof files.$inferInsert;
export type SelectFile = typeof files.$inferSelect;
export type InsertDocument = typeof documents.$inferInsert;
export type SelectDocument = typeof documents.$inferSelect;
export type InsertChat = typeof chats.$inferInsert;
export type SelectChat = typeof chats.$inferSelect;
export type InsertChatMessage = typeof chatMessages.$inferInsert;
export type SelectChatMessage = typeof chatMessages.$inferSelect;
export type InsertRagTrace = typeof ragTraces.$inferInsert;
export type SelectRagTrace = typeof ragTraces.$inferSelect;
export type InsertAnswerFeedback = typeof answerFeedback.$inferInsert;
export type SelectAnswerFeedback = typeof answerFeedback.$inferSelect;
export type InsertRagTraceChunk = typeof ragTraceChunks.$inferInsert;
export type SelectRagTraceChunk = typeof ragTraceChunks.$inferSelect;
export type InsertRagEvaluation = typeof ragEvaluations.$inferInsert;
export type SelectRagEvaluation = typeof ragEvaluations.$inferSelect;
export type InsertBillingCustomer = typeof billingCustomers.$inferInsert;
export type SelectBillingCustomer = typeof billingCustomers.$inferSelect;
export type InsertSubscription = typeof subscriptions.$inferInsert;
export type SelectSubscription = typeof subscriptions.$inferSelect;
export type InsertUsageEvent = typeof usageEvents.$inferInsert;
export type SelectUsageEvent = typeof usageEvents.$inferSelect;
export type InsertBillingCycle = typeof billingCycles.$inferInsert;
export type SelectBillingCycle = typeof billingCycles.$inferSelect;
export type InsertPaymentEvent = typeof paymentEvents.$inferInsert;
export type SelectPaymentEvent = typeof paymentEvents.$inferSelect;
export type InsertIngestionJob = typeof ingestionJobs.$inferInsert;
export type SelectIngestionJob = typeof ingestionJobs.$inferSelect;
export type SelectSourceVersion = typeof sourceVersions.$inferSelect;
export type InsertSourceVersion = typeof sourceVersions.$inferInsert;
export type SelectIndexGeneration = typeof indexGenerations.$inferSelect;
export type InsertIndexGeneration = typeof indexGenerations.$inferInsert;
export type SelectChunk = typeof chunks.$inferSelect;
export type InsertChunk = typeof chunks.$inferInsert;
