-- Compat shim: let the Scribe 1.0 build (commit 0edddb1) keep inserting rows after 0008. Idempotent.
-- Rollback: drizzle/rollback/0020_legacy_workspace_default.down.sql
--
-- Why
--   0008 made files.workspace_id, chats.workspace_id and rag_traces.workspace_id NOT NULL with no default. The 1.0 build
--   inserts those rows WITHOUT a workspace_id (createChat, reserve/upload source intake, batch init, recordRagTrace), so the
--   moment 0008 commits on a database that 1.0 still serves, every new chat, upload and RAG trace in 1.0 fails with a
--   NOT NULL violation. Without this shim a code-only rollback (promoting the 1.0 deployment) is impossible once 2.0's
--   schema is in place; only a Neon point-in-time restore would work (and it loses the data written since).
--
-- What
--   A BEFORE INSERT ... FOR EACH ROW trigger on files, chats and rag_traces, firing only WHEN (NEW.workspace_id IS NULL),
--   fills in the row's personal workspace through public.app_ensure_personal_workspace(NEW.user_id). The other 1.0 write
--   targets (chat_messages, ingestion_jobs, documents, rag_trace_chunks, rag_evaluations, usage_events) carry no
--   workspace_id and need nothing.
--
-- Guard rails (so 2.0 keeps failing loudly)
--   * The default applies only when NO workspace context is set: COALESCE(current_setting('app.workspace_id', true), '')
--     = ''. A 2.0 scoped write (scribe_app, scoped()/set_config) that forgets workspace_id still gets the NOT NULL error.
--     The legacy build never sets app.workspace_id (it connects as the owner and ignores RLS).
--   * A NULL or empty user_id leaves workspace_id NULL, so the NOT NULL error is unchanged and no workspace is created.
--   * An explicit workspace_id (everything 2.0 writes, eval workspaces) is never touched: the trigger WHEN clause skips it.
--   * RLS is unchanged. scribe_app without a context still cannot insert (WITH CHECK fails and the statement, including
--     the workspace the trigger just created, is rolled back).
--
-- Ownership: the function is SECURITY DEFINER and owned by scribe_definer like the 0013 functions. It calls
--   app_ensure_personal_workspace (also scribe_definer's, with definer_access policies on workspaces and
--   workspace_members), so it keeps working after drizzle/deferred/force_rls.sql. 0013's P0-1 block is repeated so a
--   non-superuser CREATEROLE migrator (Neon) can replace and re-own the function on re-runs. Statement order matters
--   for that second run: membership, CREATE on schema, function, ACL, OWNER TO, triggers, revoke CREATE.
--
-- Retirement: this shim exists only while 1.0 is a rollback target. Drop it with a later migration (and delete this
--   file's rollback twin) once 1.0 is no longer a rollback target.
-- Known gap (closed by the 1.0.1 rollback build, not by this migration): 1.0's source-intake does
--   `INSERT INTO ingestion_jobs ... ON CONFLICT ("file_id")`, which needs a unique index on ingestion_jobs(file_id) that
--   0014 dropped for per-version jobs. Branch rollback/1.0-compat (1.0.1 = 0edddb1 + a bare `ON CONFLICT DO NOTHING`) fixes it
--   in the app; the unpatched 0edddb1 build must not be promoted once 0008 is applied. See docs/scribe2/neon-rehearsal.md.

-- P0-1 (see 0013): membership of scribe_definer for a non-superuser migrator, and CREATE on schema public lent for this file.
DO $$
BEGIN
  IF (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    RETURN;
  END IF;
  IF current_setting('server_version_num')::int >= 160000 THEN
    IF NOT (pg_has_role(current_user, 'scribe_definer', 'USAGE') AND pg_has_role(current_user, 'scribe_definer', 'SET')) THEN
      EXECUTE format('GRANT scribe_definer TO %I WITH INHERIT TRUE, SET TRUE', current_user);
    END IF;
  ELSIF NOT pg_has_role(current_user, 'scribe_definer', 'USAGE') THEN
    EXECUTE format('GRANT scribe_definer TO %I', current_user);
  END IF;
END $$;
--> statement-breakpoint
GRANT CREATE ON SCHEMA public TO scribe_definer;
--> statement-breakpoint

-- SECURITY: runs as scribe_definer. NEW.user_id is the row's own user_id (written by the legacy build from the Clerk
-- session), never a separate argument, so no caller can ask for another user's workspace through this function.
CREATE OR REPLACE FUNCTION public.app_legacy_default_workspace()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NEW.workspace_id IS NULL
     AND COALESCE(current_setting('app.workspace_id', true), '') = ''
     AND NEW.user_id IS NOT NULL AND NEW.user_id <> '' THEN
    NEW.workspace_id := public.app_ensure_personal_workspace(NEW.user_id);
  END IF;
  RETURN NEW;
END $$;
--> statement-breakpoint

-- privileges first: after OWNER TO the migrating role no longer owns the function and cannot change its ACL
REVOKE ALL ON FUNCTION public.app_legacy_default_workspace() FROM PUBLIC;
--> statement-breakpoint
ALTER FUNCTION public.app_legacy_default_workspace() OWNER TO scribe_definer;
--> statement-breakpoint

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['files', 'chats', 'rag_traces'] LOOP
    CONTINUE WHEN to_regclass('public.' || t) IS NULL;
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I', t || '_legacy_default_workspace', t);
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE INSERT ON public.%I FOR EACH ROW WHEN (NEW.workspace_id IS NULL) '
      'EXECUTE FUNCTION public.app_legacy_default_workspace()', t || '_legacy_default_workspace', t);
  END LOOP;
END $$;
--> statement-breakpoint

REVOKE CREATE ON SCHEMA public FROM scribe_definer;
