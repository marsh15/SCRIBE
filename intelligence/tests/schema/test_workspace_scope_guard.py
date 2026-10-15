"""Guard: every public table with an FK into a workspace-scoped parent must either carry its own workspace_id column
(and therefore get a policy plus a composite FK) or have an RLS policy that scopes it some other way (EXISTS on the
parent). A new table that has neither is silently readable across workspaces under scribe_app. Fix by adding a
workspace_id column + migration (policy, composite FK) or an explicit inherit policy; do not allowlist lightly."""
import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db

pytestmark = needs_db

# Tables that are intentionally not workspace data even though they reference scoped tables (none today).
ALLOWLIST: set[str] = set()

# `scoped` = parent tables that carry workspace_id (or are `workspaces` itself).
SQL = """
with scoped as (
  select c.oid, c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind = 'r'
     and (c.relname = 'workspaces' or exists (select 1 from pg_attribute a where a.attrelid = c.oid and a.attname = 'workspace_id' and not a.attisdropped))
)
select distinct child.relname,
       exists (select 1 from pg_attribute a where a.attrelid = child.oid and a.attname = 'workspace_id' and not a.attisdropped) as has_ws,
       child.relrowsecurity and exists (select 1 from pg_policy p where p.polrelid = child.oid) as has_policy
  from pg_constraint k
  join pg_class child on child.oid = k.conrelid
  join pg_namespace n on n.oid = child.relnamespace and n.nspname = 'public'
  join scoped parent on parent.oid = k.confrelid
 where k.contype = 'f'
"""


def test_every_table_referencing_a_scoped_parent_is_workspace_protected():
    with psycopg.connect(DATABASE_URL_TEST) as c:
        rows = c.execute(SQL).fetchall()
        # tables that reference a table that is only scoped via a policy (e.g. rag_trace_chunks -> rag_traces is direct;
        # chat_messages -> chats) are covered by the same query; second hop: children of policy-only tables.
        policy_only = {r[0] for r in rows if not r[1] and r[2]}
        if policy_only:
            second = c.execute(
                """select distinct child.relname, exists (select 1 from pg_attribute a where a.attrelid = child.oid and a.attname='workspace_id' and not a.attisdropped),
                          child.relrowsecurity and exists (select 1 from pg_policy p where p.polrelid = child.oid)
                     from pg_constraint k join pg_class child on child.oid = k.conrelid
                     join pg_namespace n on n.oid = child.relnamespace and n.nspname='public'
                     join pg_class parent on parent.oid = k.confrelid
                    where k.contype='f' and parent.relname = any(%s)""", (sorted(policy_only),)).fetchall()
            rows = rows + second
    bad = sorted({r[0] for r in rows if r[0] not in ALLOWLIST and not r[1] and not r[2]})
    assert not bad, (
        f"tables with an FK into a workspace-scoped parent but neither a workspace_id column nor an RLS policy: {bad}. "
        "Add workspace_id + policy + composite FK in a new migration, or an inherit policy (see drizzle/0013)."
    )


def test_guard_actually_detects_an_unprotected_child():
    with psycopg.connect(DATABASE_URL_TEST) as c:
        c.execute("create table zz_guard_probe(id serial primary key, file_id int references files(id))")
        try:
            names = {r[0] for r in c.execute(SQL).fetchall() if not r[1] and not r[2]}
            assert "zz_guard_probe" in names
        finally:
            c.rollback()


def test_known_inherit_tables_are_indeed_policy_protected():
    with psycopg.connect(DATABASE_URL_TEST) as c:
        for t in ("chat_messages", "ingestion_jobs", "documents", "rag_trace_chunks", "rag_evaluations"):
            ok = c.execute("select relrowsecurity and exists (select 1 from pg_policy p where p.polrelid = c.oid) from pg_class c "
                           "where c.relname=%s and c.relnamespace='public'::regnamespace", (t,)).fetchone()[0]
            assert ok, t
