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

export const files = pgTable(
  "files",
  {
    id: serial("id").primaryKey(),
    name: text("name").notNull(),
    type: text("type").notNull(),
    size: integer("size").notNull(),
    userId: text("user_id").notNull(),
    fileData: text("file_data"), // legacy base64-encoded original
    extractedText: text("extracted_text"),
    storageKey: text("storage_key"),
    storageUrl: text("storage_url"),
    status: text("status", { enum: SOURCE_STATUSES }).default("ready").notNull(),
    processingError: text("processing_error"),
    textBytes: integer("text_bytes"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => ({
    statusUpdatedIndex: index("files_status_updated_at_index").on(
      table.status,
      table.updatedAt
    ),
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
  createdAt: timestamp("created_at").defaultNow().notNull(),
  updatedAt: timestamp("updated_at").defaultNow().notNull(),
});

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
    startedAt: timestamp("started_at"),
    finishedAt: timestamp("finished_at"),
    createdAt: timestamp("created_at").defaultNow().notNull(),
    updatedAt: timestamp("updated_at").defaultNow().notNull(),
  },
  (table) => ({
    fileUnique: uniqueIndex("ingestion_jobs_file_id_unique").on(table.fileId),
    queueIndex: index("ingestion_jobs_queue_index").on(
      table.status,
      table.nextRetryAt,
      table.createdAt
    ),
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
