ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "storage_key" text;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "storage_url" text;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "status" text DEFAULT 'ready' NOT NULL;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "processing_error" text;
ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "text_bytes" integer;

CREATE TABLE IF NOT EXISTS "billing_customers" (
  "id" serial PRIMARY KEY NOT NULL,
  "user_id" text NOT NULL,
  "stripe_customer_id" text,
  "razorpay_customer_id" text,
  "default_gateway" text,
  "billing_country" text,
  "currency" text DEFAULT 'INR' NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS "billing_customers_user_id_unique" ON "billing_customers" ("user_id");

CREATE TABLE IF NOT EXISTS "subscriptions" (
  "id" serial PRIMARY KEY NOT NULL,
  "user_id" text NOT NULL,
  "plan_code" text NOT NULL,
  "gateway" text NOT NULL,
  "provider_subscription_id" text,
  "status" text DEFAULT 'inactive' NOT NULL,
  "current_period_start" timestamp,
  "current_period_end" timestamp,
  "cancel_at_period_end" boolean DEFAULT false NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "usage_events" (
  "id" serial PRIMARY KEY NOT NULL,
  "user_id" text NOT NULL,
  "metric" text NOT NULL,
  "quantity" bigint NOT NULL,
  "unit" text NOT NULL,
  "source_type" text,
  "source_id" text,
  "is_estimated" boolean DEFAULT false NOT NULL,
  "occurred_at" timestamp DEFAULT now() NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "billing_cycles" (
  "id" serial PRIMARY KEY NOT NULL,
  "user_id" text NOT NULL,
  "period_start" timestamp NOT NULL,
  "period_end" timestamp NOT NULL,
  "included_inr" double precision DEFAULT 0 NOT NULL,
  "consumed_inr" double precision DEFAULT 0 NOT NULL,
  "overage_inr" double precision DEFAULT 0 NOT NULL,
  "settled" boolean DEFAULT false NOT NULL,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "payment_events" (
  "id" serial PRIMARY KEY NOT NULL,
  "gateway" text NOT NULL,
  "event_id" text NOT NULL,
  "event_type" text,
  "payload" jsonb,
  "processed" boolean DEFAULT false NOT NULL,
  "processed_at" timestamp,
  "created_at" timestamp DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS "payment_events_gateway_event_unique" ON "payment_events" ("gateway", "event_id");

CREATE TABLE IF NOT EXISTS "ingestion_jobs" (
  "id" serial PRIMARY KEY NOT NULL,
  "file_id" integer NOT NULL,
  "status" text DEFAULT 'queued' NOT NULL,
  "attempts" integer DEFAULT 0 NOT NULL,
  "last_error" text,
  "next_retry_at" timestamp,
  "started_at" timestamp,
  "finished_at" timestamp,
  "created_at" timestamp DEFAULT now() NOT NULL,
  "updated_at" timestamp DEFAULT now() NOT NULL
);

DO $$
BEGIN
  ALTER TABLE "ingestion_jobs"
    ADD CONSTRAINT "ingestion_jobs_file_id_files_id_fk"
    FOREIGN KEY ("file_id") REFERENCES "public"."files"("id")
    ON DELETE cascade ON UPDATE no action;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;
