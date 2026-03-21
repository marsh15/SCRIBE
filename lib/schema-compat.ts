import { sql } from "drizzle-orm";
import { db } from "@/lib/db-config";

let ensureSchemaPromise: Promise<void> | null = null;

async function runSchemaCompatibilityPass() {
  // Do not attempt extension installs at request time.
  // Hosted Postgres roles often lack permission for CREATE EXTENSION even when
  // pgvector is already available and in use by the existing schema.

  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "user_id" text;
  `);
  await db.execute(sql`
    UPDATE "files" SET "user_id" = 'unknown-backfill' WHERE "user_id" IS NULL;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ALTER COLUMN "user_id" SET NOT NULL;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "file_data" text;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "extracted_text" text;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "storage_key" text;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "storage_url" text;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "status" text DEFAULT 'ready';
  `);
  await db.execute(sql`
    UPDATE "files" SET "status" = 'ready' WHERE "status" IS NULL;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ALTER COLUMN "status" SET NOT NULL;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "processing_error" text;
  `);
  await db.execute(sql`
    ALTER TABLE "files" ADD COLUMN IF NOT EXISTS "text_bytes" integer;
  `);

  await db.execute(sql`
    ALTER TABLE "chats" ADD COLUMN IF NOT EXISTS "user_id" text;
  `);
  await db.execute(sql`
    UPDATE "chats" SET "user_id" = 'unknown-backfill' WHERE "user_id" IS NULL;
  `);
  await db.execute(sql`
    ALTER TABLE "chats" ALTER COLUMN "user_id" SET NOT NULL;
  `);

  await db.execute(sql`
    ALTER TABLE "documents" ADD COLUMN IF NOT EXISTS "metadata" jsonb;
  `);
  await db.execute(sql`
    ALTER TABLE "documents"
    ALTER COLUMN "embeddings"
    TYPE vector(3072)
    USING array_fill(0, ARRAY[3072])::vector;
  `);

  await db.execute(sql`
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
  `);

  await db.execute(sql`
    DO $$
    BEGIN
      ALTER TABLE "ingestion_jobs"
        ADD CONSTRAINT "ingestion_jobs_file_id_files_id_fk"
        FOREIGN KEY ("file_id") REFERENCES "public"."files"("id")
        ON DELETE cascade ON UPDATE no action;
    EXCEPTION
      WHEN duplicate_object THEN NULL;
    END $$;
  `);
}

export async function ensureApplicationSchema() {
  if (!ensureSchemaPromise) {
    ensureSchemaPromise = runSchemaCompatibilityPass().catch((error) => {
      ensureSchemaPromise = null;
      throw error;
    });
  }

  await ensureSchemaPromise;
}
