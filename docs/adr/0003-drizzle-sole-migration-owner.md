# ADR-0003: Drizzle is the only migration owner

## Status

Accepted

## Context

Python needs the same tables as TypeScript. Two migration tools would create drift.

## Decision

Drizzle migrations (including custom SQL for FTS columns, HNSW indexes, RLS, SECURITY DEFINER functions) are the only schema source. Python uses raw SQL via psycopg 3 and never writes schema. A schema-contract test fails if expected columns are missing.

## Consequences

One migration history. Python tests need a Postgres with the migrations applied.
