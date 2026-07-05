import { sql } from "@/lib/db-config";

let documentsChunkIndexSchemaPromise: Promise<void> | null = null;

async function documentsChunkIndexExists() {
  const rows = await sql`
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'documents'
      AND column_name = 'chunk_index'
    LIMIT 1
  `;

  return rows.length > 0;
}

async function repairDocumentsChunkIndexSchema() {
  if (await documentsChunkIndexExists()) return;

  await sql`ALTER TABLE "documents" ADD COLUMN IF NOT EXISTS "chunk_index" integer`;

  await sql`
    UPDATE "documents"
    SET "chunk_index" = ("metadata" ->> 'chunkIndex')::integer
    WHERE "chunk_index" IS NULL
      AND "metadata" IS NOT NULL
      AND ("metadata" ->> 'chunkIndex') ~ '^\\d+$'
  `;

  await sql`
    WITH null_ranked AS (
      SELECT
        "id",
        "file_id",
        COALESCE(
          MAX("chunk_index") FILTER (WHERE "chunk_index" IS NOT NULL)
            OVER (PARTITION BY "file_id"),
          -1
        ) AS "max_chunk_index",
        ROW_NUMBER() OVER (PARTITION BY "file_id" ORDER BY "id") AS "rn"
      FROM "documents"
      WHERE "chunk_index" IS NULL
    )
    UPDATE "documents" AS d
    SET "chunk_index" = null_ranked."max_chunk_index" + null_ranked."rn"
    FROM null_ranked
    WHERE d."id" = null_ranked."id"
  `;

  await sql`
    WITH ranked AS (
      SELECT
        "id",
        ROW_NUMBER() OVER (
          PARTITION BY "file_id", "chunk_index"
          ORDER BY "id"
        ) AS "rn"
      FROM "documents"
    )
    DELETE FROM "documents" AS d
    USING ranked
    WHERE d."id" = ranked."id"
      AND ranked."rn" > 1
  `;

  await sql`ALTER TABLE "documents" ALTER COLUMN "chunk_index" SET NOT NULL`;

  await sql`
    CREATE UNIQUE INDEX IF NOT EXISTS "documents_file_id_chunk_index_unique"
    ON "documents" ("file_id", "chunk_index")
  `;
}

export async function ensureDocumentsChunkIndexSchema() {
  documentsChunkIndexSchemaPromise ??= repairDocumentsChunkIndexSchema().catch((error) => {
    documentsChunkIndexSchemaPromise = null;
    throw error;
  });

  return documentsChunkIndexSchemaPromise;
}
