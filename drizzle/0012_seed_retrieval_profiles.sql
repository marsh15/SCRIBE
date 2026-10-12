-- Scribe 2.0 T14: seed retrieval profiles A-E (intelligence/src/scribe_intel/retrieval/profiles.py).
-- config_hash is sha256 of the Python canonical JSON (sorted keys, compact separators), NOT the
-- sha256(jsonb::text) used by 0011; values below are generated from `profiles.seed_sql()` and a test
-- asserts they match the code. Rows are immutable: a changed setting is a new name/id.
-- Idempotent (ON CONFLICT DO NOTHING); the DO block then fails the migration if a pre-existing row with
-- the same id or name holds a different config/config_hash (it would silently stay and mislink traces).
-- The seed values are listed once, in a temp table that the insert and the check both read.

DROP TABLE IF EXISTS "_seed_retrieval_profiles";

CREATE TEMP TABLE "_seed_retrieval_profiles" AS
SELECT v.id, v.name, v.config, v.config_hash
FROM (VALUES
  ('a0000000-0000-4000-8000-000000000001', 'dense-only',
   '{"budget_tokens":6000,"dedup":true,"dense_top_k":40,"expand_parents":false,"fusion":"rrf","lexical_top_k":null,"rerank":false,"rerank_model":null,"rerank_top_n":20,"rrf_k":60,"schema_version":1}',
   '96bb43a1b30381e8d71d8c7f577663da44f9e8a7f6f47bdcdf8fa0393f70170b'),
  ('a0000000-0000-4000-8000-000000000002', 'lexical-only',
   '{"budget_tokens":6000,"dedup":true,"dense_top_k":null,"expand_parents":false,"fusion":"rrf","lexical_top_k":40,"rerank":false,"rerank_model":null,"rerank_top_n":20,"rrf_k":60,"schema_version":1}',
   '4800e93dd887e5d8a37be200dd29183c412231b876ca340618f5e9a47ebb9ece'),
  ('a0000000-0000-4000-8000-000000000003', 'hybrid-rrf',
   '{"budget_tokens":6000,"dedup":true,"dense_top_k":40,"expand_parents":false,"fusion":"rrf","lexical_top_k":40,"rerank":false,"rerank_model":null,"rerank_top_n":20,"rrf_k":60,"schema_version":1}',
   '645b00f134edb5e06523f1f767e5dd82818870323f2f9d1364b6e61367eeecca'),
  ('a0000000-0000-4000-8000-000000000004', 'dense-rerank',
   '{"budget_tokens":6000,"dedup":true,"dense_top_k":40,"expand_parents":false,"fusion":"rrf","lexical_top_k":null,"rerank":true,"rerank_model":"rerank-2.5","rerank_top_n":20,"rrf_k":60,"schema_version":1}',
   'ba3010453440ce650047a11a5309f229c9be68602649254b097cf572b33de0ea'),
  ('a0000000-0000-4000-8000-000000000005', 'hybrid-rrf-rerank',
   '{"budget_tokens":6000,"dedup":true,"dense_top_k":40,"expand_parents":true,"fusion":"rrf","lexical_top_k":40,"rerank":true,"rerank_model":"rerank-2.5","rerank_top_n":20,"rrf_k":60,"schema_version":1}',
   '0117d32b812ec48088c8e0026523a05e7a08fb713853e3eace4f554d67050d12')
) AS v(id, name, config, config_hash);

INSERT INTO "retrieval_profiles" ("id", "name", "config", "config_hash")
SELECT s.id::uuid, s.name, s.config::jsonb, s.config_hash
FROM "_seed_retrieval_profiles" s
ON CONFLICT DO NOTHING;

DO $$
DECLARE
  bad text;
BEGIN
  SELECT string_agg(s.name, ', ') INTO bad
  FROM "_seed_retrieval_profiles" s
  WHERE NOT EXISTS (
    SELECT 1 FROM "retrieval_profiles" p
    WHERE p.id = s.id::uuid AND p.name = s.name
      AND p.config = s.config::jsonb AND p.config_hash = s.config_hash);
  IF bad IS NOT NULL THEN
    RAISE EXCEPTION 'retrieval_profiles seed mismatch (existing row differs from the seed): %', bad;
  END IF;
END $$;

DROP TABLE "_seed_retrieval_profiles";
