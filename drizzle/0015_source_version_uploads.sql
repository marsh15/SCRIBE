-- T19: upload a new version of a Source without losing the searchable one (DESIGN B2/B3, INV-6).
-- Hand-written and idempotent (CREATE OR REPLACE only). Rollback: drizzle/rollback/0015_source_version_uploads.down.sql
-- Never `drizzle-kit push` over RLS objects.
--
-- 1. source_versions freeze (INV-6). A version is RESERVED (upload_state='uploading') before its bytes exist, so the
--    columns that describe the bytes (storage_key, sha256, size_bytes, mime_type, duplicate_of) and upload_state itself
--    may change while the row is 'uploading'. The moment it leaves 'uploading' (-> stored | failed) the whole row is
--    frozen, and a 'stored' row must carry a sha256. identity columns are frozen from the start.
-- 2. scribe_reserve_source_version / scribe_complete_source_version: the two atomic state transitions, as SECURITY
--    INVOKER functions (deliberately not named app_*: that prefix is the hardened SECURITY DEFINER set). They set the
--    workspace context themselves, so they work for the owner connection today and for scribe_app after the RLS rollout
--    (0013 granted it DML on these tables). Keeping the SQL in the database lets the integration tests exercise exactly
--    what the neon-http driver (no multi-statement transactions) calls.
--
-- Where the blob lives: source_versions.storage_key holds the private blob URL for versions created by this flow (the
-- Python step downloads it directly). Version 1 rows created by register_version keep the pathname copied from files.

CREATE OR REPLACE FUNCTION scribe_guard_source_version_update() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.source_id IS DISTINCT FROM OLD.source_id
     OR NEW.workspace_id IS DISTINCT FROM OLD.workspace_id
     OR NEW.version_number IS DISTINCT FROM OLD.version_number
     OR NEW.created_by IS DISTINCT FROM OLD.created_by
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'source_versions row % is immutable (identity columns never change)', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF OLD.upload_state <> 'uploading'
     AND (NEW.storage_key IS DISTINCT FROM OLD.storage_key
          OR NEW.sha256 IS DISTINCT FROM OLD.sha256
          OR NEW.size_bytes IS DISTINCT FROM OLD.size_bytes
          OR NEW.mime_type IS DISTINCT FROM OLD.mime_type
          OR NEW.duplicate_of IS DISTINCT FROM OLD.duplicate_of
          OR NEW.upload_state IS DISTINCT FROM OLD.upload_state) THEN
    RAISE EXCEPTION 'source_versions row % is immutable once stored or failed', OLD.id
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF NEW.upload_state = 'stored' AND NEW.sha256 IS NULL THEN
    RAISE EXCEPTION 'source_versions row % cannot be stored without a sha256', OLD.id
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$fn$;
--> statement-breakpoint

-- Reserve the next version number of a live Source that already has a searchable (active) version.
-- Serialised by locking the files row, which register_version also locks before numbering.
-- Errors (message is the code): SOURCE_NOT_FOUND (P0002), NO_ACTIVE_VERSION, TYPE_MISMATCH (P0001).
CREATE OR REPLACE FUNCTION public.scribe_reserve_source_version(
  p_workspace uuid, p_source integer, p_user text, p_mime text, p_size bigint)
RETURNS TABLE(out_version_id uuid, out_version_number integer)
LANGUAGE plpgsql SET search_path = pg_catalog, public
AS $$
DECLARE
  f record;
  n integer;
  vid uuid;
BEGIN
  PERFORM set_config('app.workspace_id', p_workspace::text, true);
  SELECT x.active_version_id, x.deleted_at, x.type INTO f
    FROM public.files x
   WHERE x.id = p_source AND x.workspace_id = p_workspace AND x.user_id = p_user
     FOR UPDATE;
  IF NOT FOUND OR f.deleted_at IS NOT NULL THEN
    RAISE EXCEPTION 'SOURCE_NOT_FOUND' USING ERRCODE = 'P0002';
  END IF;
  IF f.active_version_id IS NULL THEN
    RAISE EXCEPTION 'NO_ACTIVE_VERSION' USING ERRCODE = 'P0001';
  END IF;
  IF f.type IS DISTINCT FROM p_mime THEN
    RAISE EXCEPTION 'TYPE_MISMATCH' USING ERRCODE = 'P0001';
  END IF;
  -- reservations whose upload never completed stop counting after a day
  UPDATE public.source_versions
     SET upload_state = 'failed'
   WHERE source_id = p_source AND workspace_id = p_workspace
     AND upload_state = 'uploading' AND created_at < now() - interval '24 hours';
  SELECT COALESCE(max(v.version_number), 0) + 1 INTO n
    FROM public.source_versions v WHERE v.source_id = p_source AND v.workspace_id = p_workspace;
  INSERT INTO public.source_versions(source_id, workspace_id, version_number, size_bytes, mime_type, upload_state, created_by)
  VALUES (p_source, p_workspace, n, p_size, p_mime, 'uploading', p_user)
  RETURNING id INTO vid;
  RETURN QUERY SELECT vid, n;
END $$;
--> statement-breakpoint

-- Finish a reservation once the bytes are stored and hashed. One transaction (the function body):
--   * lock the Source row, then the version row; anything not 'uploading' is a REPLAY and changes nothing
--     (a duplicate Blob callback therefore yields one version and one job);
--   * a deleted Source fails the reservation;
--   * same sha256 as the active version, or as a version whose build is still queued/running, marks the row
--     duplicate_of and builds nothing. Same bytes as the ACTIVE version also points desired_version_id back at the
--     active version, which cancels any newer in-flight build at its next step boundary;
--   * otherwise desired_version_id := this version and ONE ingestion job is queued (unique per file+version).
-- outcome: queued | duplicate | replayed | source_deleted | not_found
CREATE OR REPLACE FUNCTION public.scribe_complete_source_version(
  p_workspace uuid, p_version uuid, p_url text, p_sha256 text, p_size bigint)
RETURNS TABLE(outcome text, out_version_number integer, out_source_id integer, out_duplicate_of uuid)
LANGUAGE plpgsql SET search_path = pg_catalog, public
AS $$
DECLARE
  v_source integer;
  v_num integer;
  v_state text;
  v_dup uuid;
  f record;
  dup uuid;
BEGIN
  PERFORM set_config('app.workspace_id', p_workspace::text, true);
  SELECT x.source_id, x.version_number INTO v_source, v_num
    FROM public.source_versions x WHERE x.id = p_version AND x.workspace_id = p_workspace;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found'::text, NULL::integer, NULL::integer, NULL::uuid;
    RETURN;
  END IF;
  SELECT x.active_version_id, x.desired_version_id, x.deleted_at INTO f
    FROM public.files x WHERE x.id = v_source AND x.workspace_id = p_workspace FOR UPDATE;
  SELECT x.upload_state, x.duplicate_of INTO v_state, v_dup  -- re-read under the Source lock
    FROM public.source_versions x WHERE x.id = p_version FOR UPDATE;
  IF v_state <> 'uploading' THEN
    RETURN QUERY SELECT 'replayed'::text, v_num, v_source, v_dup;
    RETURN;
  END IF;
  IF f.deleted_at IS NOT NULL THEN
    UPDATE public.source_versions SET upload_state = 'failed' WHERE id = p_version;
    RETURN QUERY SELECT 'source_deleted'::text, v_num, v_source, NULL::uuid;
    RETURN;
  END IF;

  SELECT a.id INTO dup
    FROM public.source_versions a
   WHERE a.source_id = v_source AND a.workspace_id = p_workspace AND a.id <> p_version
     AND a.sha256 = p_sha256 AND a.upload_state = 'stored' AND a.duplicate_of IS NULL
     AND (a.id = f.active_version_id
          OR (a.id = f.desired_version_id AND EXISTS (
                SELECT 1 FROM public.ingestion_jobs j
                 WHERE j.source_version_id = a.id AND j.status IN ('queued', 'processing', 'retrying'))))
   ORDER BY (a.id = f.active_version_id) DESC
   LIMIT 1;

  UPDATE public.source_versions
     SET storage_key = p_url, sha256 = p_sha256, size_bytes = p_size, upload_state = 'stored', duplicate_of = dup
   WHERE id = p_version;

  IF dup IS NOT NULL THEN
    IF dup = f.active_version_id AND f.desired_version_id IS DISTINCT FROM dup THEN
      UPDATE public.files SET desired_version_id = dup, updated_at = now()
       WHERE id = v_source AND workspace_id = p_workspace;
    END IF;
    RETURN QUERY SELECT 'duplicate'::text, v_num, v_source, dup;
    RETURN;
  END IF;

  UPDATE public.files SET desired_version_id = p_version, updated_at = now()
   WHERE id = v_source AND workspace_id = p_workspace;
  INSERT INTO public.ingestion_jobs(file_id, source_version_id, status, attempts, created_at, updated_at)
  VALUES (v_source, p_version, 'queued', 0, now(), now())
  ON CONFLICT DO NOTHING;
  RETURN QUERY SELECT 'queued'::text, v_num, v_source, NULL::uuid;
END $$;
--> statement-breakpoint

REVOKE ALL ON FUNCTION public.scribe_reserve_source_version(uuid, integer, text, text, bigint),
  public.scribe_complete_source_version(uuid, uuid, text, text, bigint) FROM PUBLIC;
--> statement-breakpoint
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'scribe_app') THEN
    GRANT EXECUTE ON FUNCTION public.scribe_reserve_source_version(uuid, integer, text, text, bigint),
      public.scribe_complete_source_version(uuid, uuid, text, text, bigint) TO scribe_app;
  END IF;
END $$;
