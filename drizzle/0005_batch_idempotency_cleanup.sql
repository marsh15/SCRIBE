ALTER TABLE "documents" ADD COLUMN IF NOT EXISTS "chunk_index" integer;

UPDATE "documents"
SET "chunk_index" = ("metadata" ->> 'chunkIndex')::integer
WHERE "chunk_index" IS NULL
  AND "metadata" IS NOT NULL
  AND ("metadata" ->> 'chunkIndex') ~ '^\d+$';

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
WHERE d."id" = null_ranked."id";

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
  AND ranked."rn" > 1;

ALTER TABLE "documents" ALTER COLUMN "chunk_index" SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS "documents_file_id_chunk_index_unique"
  ON "documents" ("file_id", "chunk_index");

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name = 'billing_customers'
  ) THEN
    ALTER TABLE "billing_customers" DROP COLUMN IF EXISTS "stripe_customer_id";
  END IF;
END $$;
