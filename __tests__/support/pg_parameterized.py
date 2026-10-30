"""Test-only parameterized PostgreSQL transaction bridge for Drizzle's pg-proxy."""
import json
import re
import sys

import psycopg

payload = json.load(sys.stdin)
try:
    with psycopg.connect(payload["dsn"], autocommit=True) as conn:
        results = []
        error = None
        try:
            with conn.transaction():
                for query in payload["queries"]:
                    ordered = []
                    def param(match):
                        ordered.append(query["params"][int(match.group(1)) - 1])
                        return "%s"
                    statement = re.sub(r"\$(\d+)", param, query["sql"])
                    with conn.cursor() as cursor:
                        cursor.execute(statement, ordered)
                        results.append(cursor.fetchall() if cursor.description else [])
        except psycopg.Error:
            error = "test transaction rejected"
        # Reuse the same physical connection after commit/rollback: a session-scoped context would leak here.
        context_after = conn.execute("select coalesce(current_setting('app.workspace_id', true), '')").fetchone()[0]
    print(json.dumps({"results": results, "error": error, "context_after": context_after}, default=str))
except Exception as exc:
    print(json.dumps({"error": str(exc)}))
    sys.exit(1)
