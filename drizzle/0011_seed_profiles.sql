-- Scribe 2.0 T08: seed profiles. Idempotent (ON CONFLICT DO NOTHING on id or name).
-- Profile rows are immutable (0010 triggers), so a changed setting means a new
-- row with a new name/id, never an edit of these. Stable UUIDs let code and
-- tests reference them directly. retrieval_profiles A-E are seeded by T14.

INSERT INTO "embedding_profiles" ("id", "name", "provider", "model", "dimensions", "normalized", "config", "config_hash")
SELECT v.id::uuid, v.name, v.provider, v.model, v.dimensions, v.normalized, v.config::jsonb,
       encode(sha256(convert_to(v.config::jsonb::text, 'UTF8')), 'hex')
FROM (VALUES
  ('e0000000-0000-4000-8000-000000000001', 'legacy-gemini-embedding-2-preview-3072', 'google',
   'models/gemini-embedding-2-preview', 3072, false,
   '{"space":"legacy-3072","task_types":{"document":"RETRIEVAL_DOCUMENT","query":"RETRIEVAL_QUERY"},"exact_search_only":true}'),
  ('e0000000-0000-4000-8000-000000000002', 'gemini-embedding-2-1536', 'google',
   'models/gemini-embedding-2', 1536, true,
   '{"space":"v2-1536","output_dimensionality":1536,"task_types":{"document":"RETRIEVAL_DOCUMENT","query":"RETRIEVAL_QUERY"},"renormalize":true}')
) AS v(id, name, provider, model, dimensions, normalized, config)
ON CONFLICT DO NOTHING;

INSERT INTO "chunk_profiles" ("id", "name", "config", "config_hash")
SELECT v.id::uuid, v.name, v.config::jsonb,
       encode(sha256(convert_to(v.config::jsonb::text, 'UTF8')), 'hex')
FROM (VALUES
  ('c0000000-0000-4000-8000-000000000001', 'legacy-char-4000-400',
   '{"strategy":"recursive_character","chunk_size_chars":4000,"chunk_overlap_chars":400,"parents":false}'),
  ('c0000000-0000-4000-8000-000000000002', 'structural-v1',
   '{"strategy":"structural","child_max_tokens":384,"overlap_tokens":48,"overlap_only_when_block_split":true,"parent_target_tokens":1536,"parent_level":"section","tokenizer":"o200k_base","tokenizer_is_proxy":true,"embed_heading_path":true}')
) AS v(id, name, config)
ON CONFLICT DO NOTHING;

INSERT INTO "index_profiles" ("id", "name", "parser_profile", "chunk_profile_id", "embedding_profile_id", "config")
SELECT v.id::uuid, v.name, v.parser_profile, v.chunk_profile_id::uuid, v.embedding_profile_id::uuid, v.config::jsonb
FROM (VALUES
  ('10000000-0000-4000-8000-000000000001', 'legacy-v1', 'legacy-ts-v1',
   'c0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-000000000001',
   '{"vector_table":"chunk_embeddings_3072","ann":false}'),
  ('10000000-0000-4000-8000-000000000002', 'structural-v1', 'structural-v1',
   'c0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-000000000002',
   '{"vector_table":"chunk_embeddings_1536","ann":"hnsw"}')
) AS v(id, name, parser_profile, chunk_profile_id, embedding_profile_id, config)
ON CONFLICT DO NOTHING;
