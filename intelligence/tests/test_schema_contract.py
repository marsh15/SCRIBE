"""Python never writes schema (ADR-0003). This test pins the columns Python
relies on; extend EXPECTED as Drizzle migrations add tables Python reads.
Requires a database migrated by Drizzle (`npx drizzle-kit migrate`)."""
import psycopg
from conftest import DATABASE_URL_TEST, needs_db

EXPECTED: dict[str, set[str]] = {
    "files": {
        "id", "name", "type", "size", "user_id", "file_data", "extracted_text",
        "storage_key", "storage_url", "status", "processing_error", "text_bytes",
        "created_at", "updated_at",
    },
    "documents": {"id", "file_id", "chunk_index", "content", "metadata", "embeddings"},
    "ingestion_jobs": {
        "id", "file_id", "status", "attempts", "last_error", "next_retry_at",
        "started_at", "finished_at", "created_at", "updated_at",
    },
}


@needs_db
def test_expected_columns_exist():
    with psycopg.connect(DATABASE_URL_TEST, prepare_threshold=None) as conn:
        rows = conn.execute(
            "select table_name, column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = any(%s)",
            (list(EXPECTED),),
        ).fetchall()
    actual: dict[str, set[str]] = {}
    for table, column in rows:
        actual.setdefault(table, set()).add(column)
    missing = {
        t: sorted(cols - actual.get(t, set()))
        for t, cols in EXPECTED.items()
        if cols - actual.get(t, set())
    }
    assert not missing, (
        f"schema drift: columns missing from DB {missing}. "
        "Did you run `npx drizzle-kit migrate` against DATABASE_URL_TEST?"
    )
