"""Parent chunk loading for parent expansion (DESIGN B4 step 8, T14).

Parents are the `chunks` rows with `parent_id IS NULL`. They carry no embeddings and are never
retrieval hits; they are only fetched to replace a retrieved child's text.
"""

from __future__ import annotations

import uuid
from typing import Sequence

import psycopg

from .packing import ParentContent
from .queries import SCOPE_JOINS, scope_params


async def load_parents(
    conn: psycopg.AsyncConnection, workspace_id: str, index_profile_id: uuid.UUID | str,
    parent_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ParentContent]:
    """Load parent chunks by id with the same scoping as the child retrievers.

    ONE statement; workspace on every table, generation ACTIVE for the index profile, the
    `active_indexes` pointer and a non-deleted file are all in its JOIN/WHERE (INV-1/2/3/14), so an
    id from another workspace, a superseded/building generation, a deleted file, or a child chunk
    id simply returns nothing.

    `conn` MUST already be inside `snapshot_tx` (the same REPEATABLE READ transaction that served
    the dense/lexical children), so the parents come from the same snapshot as the children:
    a re-ingest that activates a new generation mid-request cannot pair old children with new
    parents. Empty `parent_ids` returns {} without a query.
    """
    ids = list(dict.fromkeys(parent_ids))
    if not ids:
        return {}
    params = scope_params(workspace_id, index_profile_id, None)
    params["ids"] = ids
    cur = await conn.execute(
        f"""select c.id, c.content, c.heading_text
            from chunks c {SCOPE_JOINS}
            where c.workspace_id = %(w)s and c.parent_id is null and c.id = any(%(ids)s)""",
        params)
    return {cid: ParentContent(content, heading) for cid, content, heading in await cur.fetchall()}
