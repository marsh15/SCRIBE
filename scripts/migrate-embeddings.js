// Migration: change embeddings column from 768 to 3072 dimensions
// (switching from text-embedding-004 back to gemini-embedding-001)
const { neon } = require("@neondatabase/serverless");
require("dotenv").config({ path: ".env" });

async function migrate() {
    const sql = neon(process.env.NEON_DATABASE_URL);

    console.log("Dropping existing embeddings data and column...");

    // Must drop existing rows first since postgres can't cast between vector dims
    await sql`DELETE FROM documents`;
    await sql`ALTER TABLE documents DROP COLUMN IF EXISTS embeddings`;

    console.log("Re-adding embeddings column with 3072 dimensions...");
    await sql`ALTER TABLE documents ADD COLUMN embeddings vector(3072) NOT NULL DEFAULT array_fill(0, ARRAY[3072])::vector`;
    await sql`ALTER TABLE documents ALTER COLUMN embeddings DROP DEFAULT`;

    // Also clean up orphaned files since their documents were deleted
    await sql`DELETE FROM files WHERE id NOT IN (SELECT DISTINCT file_id FROM documents)`;

    console.log("✅ Migration complete. Re-upload your documents.");
    process.exit(0);
}

migrate().catch((e) => {
    console.error("Migration failed:", e);
    process.exit(1);
});
