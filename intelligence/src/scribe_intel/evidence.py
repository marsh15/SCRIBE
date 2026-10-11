"""Evidence reads that respect soft delete (T20; DESIGN INV-8 and INV-14).

An evidence item (an `[E#]` quote with page / heading) is readable only by its own workspace and only while its Source
is not deleted. Once the Source is soft-deleted the citation still resolves, but as "Source deleted": the quote, page,
heading, hash and Source name are withheld (they are copies of the deleted document's text). After the purge the
version link is NULL (FK ON DELETE SET NULL) and the row is treated the same way.

Anything that shows or verifies an evidence quote (T23 citation opening, T25 evidence bundles, T26 claim verification)
MUST read through `read_evidence_items` / `read_evidence_bundle` and must not select `evidence_items.quote` directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

import psycopg

from scribe_ingest.indexer import _begin

SOURCE_DELETED_LABEL = "Source deleted"


@dataclass(frozen=True)
class EvidenceView:
    id: uuid.UUID
    bundle_id: uuid.UUID
    label: str  # the [E#] label is kept so existing citations still point at something
    status: str  # "available" | "source_deleted"
    source_id: int | None = None
    source_name: str | None = None
    quote: str | None = None
    quote_hash: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    heading_path: tuple[str, ...] | None = None
    notice: str | None = None

    @property
    def readable(self) -> bool:
        return self.status == "available"


_SELECT = """
select e.id, e.bundle_id, e.label, e.quote, e.quote_hash, e.page_start, e.page_end, e.heading_path,
       f.id, f.name, f.deleted_at
from evidence_items e
left join source_versions v on v.id = e.source_version_id and v.workspace_id = %(w)s
left join files f on f.id = v.source_id and f.workspace_id = %(w)s
where e.workspace_id = %(w)s and {cond}
order by e.bundle_id, e.label
"""


def _view(row) -> EvidenceView:
    eid, bundle_id, label, quote, qhash, p0, p1, heading, fid, fname, fdeleted = row
    if fid is None or fdeleted is not None:
        return EvidenceView(eid, bundle_id, label, "source_deleted", notice=SOURCE_DELETED_LABEL)
    return EvidenceView(eid, bundle_id, label, "available", source_id=fid, source_name=fname, quote=quote,
                        quote_hash=qhash, page_start=p0, page_end=p1,
                        heading_path=tuple(heading) if heading else None)


async def _run(conn: psycopg.AsyncConnection, workspace_id: str, cond: str, params: dict) -> list[EvidenceView]:
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute(_SELECT.format(cond=cond), {"w": str(workspace_id), **params})
        return [_view(r) for r in await cur.fetchall()]


async def read_evidence_items(conn: psycopg.AsyncConnection, workspace_id: str,
                              item_ids: Sequence[uuid.UUID]) -> list[EvidenceView]:
    """Items of this workspace only; an id from another workspace is simply absent (the caller answers 404)."""
    if not item_ids:
        return []
    return await _run(conn, workspace_id, "e.id = any(%(ids)s)", {"ids": list(item_ids)})


async def read_evidence_bundle(conn: psycopg.AsyncConnection, workspace_id: str,
                               bundle_id: uuid.UUID) -> list[EvidenceView]:
    return await _run(conn, workspace_id, "e.bundle_id = %(b)s", {"b": bundle_id})
