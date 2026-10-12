-- DEFERRED + DESTRUCTIVE (not journaled, never run by drizzle or apply-migrations.sh). T22 / DESIGN D20.
-- Retire the legacy 3072-dim embeddings once every existing Source lives on structural-v1. Run by hand with psql:
--
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -v dry_run=1 -f drizzle/deferred/retire_legacy.sql   # runs, then ROLLBACKs
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1                 -f drizzle/deferred/retire_legacy.sql   # real run, COMMITs
--
-- PREREQUISITES (do all of them first)
--   1. Create a Neon branch of the production branch NOW. That branch is the backup; see RESTORE at the bottom.
--   2. `python -m scribe_ingest.upgrade backfill` has run and the tick has drained the queue:
--        `python -m scribe_ingest.upgrade progress` must say retire_ready: true (waiting_on: 0).
--   3. Run this as the table OWNER (RLS is enabled but not forced, the owner sees every workspace).
--
-- SECTION 1 (always executed): guard, then delete legacy chunk_embeddings_3072 of upgraded Sources.
-- SECTION 2 (commented out): drop the legacy `documents` table. See the warning there before you even think of it.
--
-- Scope: Sources in workspaces of kind 'eval' are exempt from the guard and are never touched, because the legacy-dense
-- baseline strategy lives there on purpose (D20: legacy 3072-dim embeddings are kept only in the eval workspace).

BEGIN;

-- ---------------------------------------------------------------------------------------------------- SECTION 1
-- GUARD. Raises (and with ON_ERROR_STOP aborts the whole script, nothing is deleted) unless EVERY active, non-deleted,
-- non-eval Source either
--   (a) has a READY or ACTIVE, fully embedded structural-v1 generation on its ACTIVE version, or
--   (b) is explicitly marked for re-upload (files.reupload_required_at, set by the backfill when the original blob is
--       missing/unreadable). Those keep their legacy embeddings: they are NOT deleted below.
DO $$
DECLARE
  structural uuid;
  n_blocking integer;
  sample text;
BEGIN
  SELECT id INTO structural FROM index_profiles WHERE name = 'structural-v1';
  IF structural IS NULL THEN
    RAISE EXCEPTION 'retire_legacy refused: index profile structural-v1 does not exist (is the database migrated?)';
  END IF;

  SELECT count(*), string_agg(s.id::text, ', ' ORDER BY s.id) FILTER (WHERE s.rn <= 10)
    INTO n_blocking, sample
  FROM (
    SELECT f.id, row_number() OVER (ORDER BY f.id) AS rn
    FROM files f
    JOIN workspaces w ON w.id = f.workspace_id
    WHERE f.deleted_at IS NULL AND w.kind <> 'eval'
      AND (f.active_version_id IS NOT NULL
           OR EXISTS (SELECT 1 FROM index_generations g WHERE g.source_id = f.id AND g.status = 'ACTIVE'))
      AND f.reupload_required_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM index_generations g
        WHERE g.source_id = f.id AND g.index_profile_id = structural AND g.source_version_id = f.active_version_id
          AND g.status IN ('READY', 'ACTIVE') AND g.expected_chunks > 0 AND g.embedded_chunks = g.expected_chunks)
  ) s;

  IF n_blocking > 0 THEN
    RAISE EXCEPTION 'retire_legacy refused: % active Source(s) are neither on a READY/ACTIVE structural-v1 generation nor marked for re-upload (first ids: %). Finish the backfill (python -m scribe_ingest.upgrade progress) first.',
      n_blocking, sample;
  END IF;
END $$;

-- DELETE legacy embeddings of upgraded Sources only. A Source that is merely marked re-upload keeps them (it is still
-- searchable through legacy-dense). The upgraded set is computed once so the later statements agree with this one.
CREATE TEMP TABLE _retire_sources ON COMMIT DROP AS
SELECT f.id AS source_id
FROM files f
JOIN workspaces w ON w.id = f.workspace_id
WHERE f.deleted_at IS NULL AND w.kind <> 'eval'
  AND EXISTS (
    SELECT 1 FROM index_generations g JOIN index_profiles ip ON ip.id = g.index_profile_id
    WHERE g.source_id = f.id AND ip.name = 'structural-v1' AND g.source_version_id = f.active_version_id
      AND g.status IN ('READY', 'ACTIVE') AND g.expected_chunks > 0 AND g.embedded_chunks = g.expected_chunks);

DO $$
DECLARE n_emb bigint; n_gen bigint; n_ptr bigint;
BEGIN
  DELETE FROM chunk_embeddings_3072 e
   USING chunks c, index_generations g, index_profiles ip
   WHERE c.id = e.chunk_id AND g.id = c.generation_id AND ip.id = g.index_profile_id
     AND ip.name = 'legacy-v1' AND g.source_id IN (SELECT source_id FROM _retire_sources);
  GET DIAGNOSTICS n_emb = ROW_COUNT;

  -- D20: a superseded generation keeps chunks and spans (old citations still resolve) but loses its embeddings.
  -- Drop the pointer first so nothing resolves "active" to an embedding-less generation, then supersede.
  DELETE FROM active_indexes ai USING index_profiles ip
   WHERE ip.id = ai.index_profile_id AND ip.name = 'legacy-v1' AND ai.source_id IN (SELECT source_id FROM _retire_sources);
  GET DIAGNOSTICS n_ptr = ROW_COUNT;
  UPDATE index_generations g SET status = 'SUPERSEDED', superseded_at = coalesce(g.superseded_at, now()), updated_at = now()
   FROM index_profiles ip
   WHERE ip.id = g.index_profile_id AND ip.name = 'legacy-v1' AND g.status = 'ACTIVE'
     AND g.source_id IN (SELECT source_id FROM _retire_sources);
  GET DIAGNOSTICS n_gen = ROW_COUNT;

  RAISE NOTICE 'retire_legacy: % legacy embedding rows deleted, % legacy generations superseded, % legacy pointers removed, % Sources upgraded, % Sources kept on legacy (re-upload)',
    n_emb, n_gen, n_ptr, (SELECT count(*) FROM _retire_sources),
    (SELECT count(*) FROM files f JOIN workspaces w ON w.id = f.workspace_id
      WHERE f.deleted_at IS NULL AND w.kind <> 'eval' AND f.reupload_required_at IS NOT NULL
        AND f.id NOT IN (SELECT source_id FROM _retire_sources));
END $$;

\if :{?dry_run}
  \echo 'dry_run set: ROLLING BACK. Nothing was changed.'
  ROLLBACK;
\else
  COMMIT;
\endif

-- Reclaim the space (VACUUM cannot run inside the transaction above). Run by hand afterwards:
--   VACUUM (ANALYZE) chunk_embeddings_3072;
-- Disk is returned to Neon's storage meter only after VACUUM FULL (takes an ACCESS EXCLUSIVE lock; do it off-hours):
--   VACUUM FULL chunk_embeddings_3072;

-- ---------------------------------------------------------------------------------------------------- SECTION 2
-- !!! DO NOT UNCOMMENT while any of these is true. At the time T22 was written ALL of them were true:
--   * lib/search.ts (RETRIEVAL_ENGINE=legacy, the default until T17 flips it) reads `documents` for chat retrieval.
--   * app/files/[id]/actions.ts (file detail page), lib/ingestion/source-intake.ts and lib/db-compat.ts use `documents`.
--   * scripts/backfill-legacy-generations.ts and scripts/eval-baseline.ts read/write `documents`.
--   * `documents.embeddings` is a SECOND copy of the 3072-dim vectors (section 1 only removed the copy in
--     chunk_embeddings_3072). Until this section runs, retiring does not shrink the DB as much as it looks.
-- Prerequisites for uncommenting: RETRIEVAL_ENGINE=python is the default and the legacy path is deleted from the code,
-- `documents` is removed from lib/db-schema.ts and every import above, and a fresh Neon branch backup exists.
-- Then run this block alone, in its own transaction, with the same psql flags.
--
-- BEGIN;
-- DO $$
-- BEGIN
--   IF EXISTS (SELECT 1 FROM files f JOIN workspaces w ON w.id = f.workspace_id
--               WHERE f.deleted_at IS NULL AND w.kind <> 'eval' AND f.reupload_required_at IS NOT NULL) THEN
--     RAISE EXCEPTION 'refusing to drop documents: Sources are still marked re-upload and would lose their only copy of the text';
--   END IF;
-- END $$;
-- DROP TABLE IF EXISTS public.documents;
-- COMMIT;

-- ---------------------------------------------------------------------------------------------------- RESTORE / DOWN
-- There is no SQL "down": deleted embeddings cannot be recomputed from this database. The backup is the Neon branch
-- created in PREREQUISITE 1 (point-in-time restore of the production branch to that moment, or copy the table back:
--   pg_dump "$BACKUP_BRANCH_URL" -t chunk_embeddings_3072 -t documents --data-only | psql "$DATABASE_URL"
-- after TRUNCATE of the target tables). The legacy chunks themselves are never deleted by this script, and Sources
-- marked re-upload still own their legacy embeddings, so only upgraded Sources depend on the backup. Superseded legacy
-- generations can be re-activated by hand after restoring embeddings:
--   UPDATE index_generations SET status = 'ACTIVE' WHERE ... AND index_profile_id = (SELECT id FROM index_profiles WHERE name = 'legacy-v1');
--   plus the matching active_indexes row.
