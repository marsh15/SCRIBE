-- Order-independent schema fingerprint for diffing two databases (column order and OIDs are ignored).
--   psql "$URL" -X -At -f scripts/neon-rehearsal/fingerprint.sql > rehearsal/fp-branch.txt
\set ON_ERROR_STOP 1
SELECT 'column ' || table_name || '.' || column_name || ' ' || data_type || ' null=' || is_nullable
       || ' default=' || coalesce(column_default, '-')
FROM information_schema.columns WHERE table_schema = 'public'
UNION ALL
SELECT 'constraint ' || conrelid::regclass || ' ' || pg_get_constraintdef(oid)
FROM pg_constraint WHERE connamespace = 'public'::regnamespace
UNION ALL
SELECT 'index ' || indexdef FROM pg_indexes WHERE schemaname = 'public'
UNION ALL
SELECT 'policy ' || tablename || ' ' || policyname || ' ' || cmd || ' ' || array_to_string(roles, ',')
       || ' using=' || coalesce(qual, '-') || ' check=' || coalesce(with_check, '-')
FROM pg_policies WHERE schemaname = 'public'
UNION ALL
SELECT 'function ' || p.oid::regprocedure || ' secdef=' || p.prosecdef || ' owner=' || CASE WHEN pg_get_userbyid(p.proowner) LIKE 'scribe\_%' THEN pg_get_userbyid(p.proowner) ELSE 'migrator' END
FROM pg_proc p WHERE p.pronamespace = 'public'::regnamespace AND p.prokind = 'f'
  AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')
UNION ALL
SELECT 'trigger ' || tgrelid::regclass || ' ' || tgname FROM pg_trigger WHERE NOT tgisinternal
  AND tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace = 'public'::regnamespace)
UNION ALL
SELECT 'rls ' || relname || ' enabled=' || relrowsecurity || ' force=' || relforcerowsecurity
FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'
ORDER BY 1;
