-- Rollback for drizzle/0019_workspace_composite_fks.sql. Not journaled; run manually with psql.
-- Drops the composite (col, workspace_id) FKs and the (id, workspace_id) unique constraints.
BEGIN;
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.conrelid::regclass AS tbl, c.conname FROM pg_constraint c
            WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace AND c.conname LIKE '%\_ws\_fk'
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
  END LOOP;
  FOR r IN SELECT c.conrelid::regclass AS tbl, c.conname FROM pg_constraint c
            WHERE c.contype = 'u' AND c.connamespace = 'public'::regnamespace AND c.conname LIKE '%\_id\_workspace\_uq'
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.tbl, r.conname);
  END LOOP;
  IF to_regclass('drizzle.__drizzle_migrations') IS NOT NULL THEN
    DELETE FROM drizzle.__drizzle_migrations WHERE created_at = 1784851200000;
  END IF;
END $$;
COMMIT;
