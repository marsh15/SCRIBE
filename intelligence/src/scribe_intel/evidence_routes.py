"""POST /evidence/open (T23). Scope `evidence_read`; the workspace comes from the token only (INV-15).

Opens one evidence item at its source location. The quote is read ONLY through `read_evidence_items` (INV-8, INV-14):
another workspace's id answers 404 (indistinguishable from an unknown id), a deleted Source answers
``{"source_deleted": true, "notice": "Source deleted"}`` with the quote, page, heading and source name withheld.
Location data (page, bbox, row) comes from the chunk's source spans. Where a span's block is OCR the bbox is dropped
here too (we never draw a box for OCR text) and the OCR notice is attached. A page is only ever reported when a span
or the evidence item recorded it: never estimated (INV-7). Document text is never logged (INV-13).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from scribe_ingest.indexer import _begin

from .auth import Principal, require_scope
from .db import get_pool
from .evidence import OCR_NOTICE, EvidenceView, read_evidence_items

router = APIRouter()

_LOCATION = """
select e.chunk_id, v.id, v.version_number, v.mime_type
from evidence_items e
join source_versions v on v.id = e.source_version_id and v.workspace_id = %(w)s
where e.id = %(id)s and e.workspace_id = %(w)s
"""

_SPANS = """
select b.block_index, s.page, s.bbox, b.ocr, b.row_number, s.char_start, s.char_end
from chunk_source_spans s
join document_blocks b on b.id = s.block_id and b.workspace_id = %(w)s
where s.chunk_id = %(c)s and s.workspace_id = %(w)s
order by b.block_index, s.char_start
"""


class OpenBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    item_id: uuid.UUID


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"error_code": "NOT_FOUND", "message": "evidence not found",
                                                  "retryable": False})


def span_dict(row) -> dict[str, Any]:
    block_index, page, bbox, ocr, row_number, c0, c1 = row
    return {"block_index": block_index, "page": page,
            # never a box for OCR text, whatever is stored (the indexer already writes NULL; this is belt and braces)
            "bbox": [float(x) for x in bbox] if bbox and not ocr and len(bbox) == 4 else None,
            "ocr": bool(ocr), "row_number": row_number, "char_start": c0, "char_end": c1}


def open_payload(view: EvidenceView, version: tuple | None, spans: list[dict[str, Any]]) -> dict[str, Any]:
    if not view.readable:
        return {"item_id": str(view.id), "label": view.label, "source_deleted": True, "notice": view.notice}
    ocr = view.ocr or any(s["ocr"] for s in spans)
    pages = [s["page"] for s in spans if s["page"] is not None]
    page = view.page_start if view.page_start is not None else (min(pages) if pages else None)
    version_id, version_number, mime = version if version else (None, None, None)
    return {
        "item_id": str(view.id), "label": view.label, "source_deleted": False,
        "source_id": view.source_id, "source_name": view.source_name,
        "source_version_id": str(version_id) if version_id else None, "version_number": version_number,
        "mime_type": mime, "quote": view.quote, "heading_path": list(view.heading_path or ()),
        "page": page,  # None -> the viewer says "page unavailable"
        "page_end": view.page_end, "ocr": ocr, "ocr_notice": OCR_NOTICE if ocr else None,
        "spans": spans,
    }


@router.post("/evidence/open")
async def open_evidence(body: OpenBody, principal: Principal = Depends(require_scope("evidence_read"))):
    wid = principal.wid
    try:
        uuid.UUID(wid)
    except ValueError:
        return JSONResponse(status_code=403, content={"error_code": "INVALID_WORKSPACE",
                                                      "message": "invalid workspace", "retryable": False})
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            views = await read_evidence_items(conn, wid, [body.item_id])
            if not views:
                return _not_found()
            view = views[0]
            version, spans = None, []
            if view.readable:
                async with conn.transaction():
                    await _begin(conn, wid)
                    cur = await conn.execute(_LOCATION, {"w": wid, "id": body.item_id})
                    loc = await cur.fetchone()
                    if loc is not None:
                        chunk_id, *version_cols = loc
                        version = tuple(version_cols)
                        if chunk_id is not None:
                            cur = await conn.execute(_SPANS, {"w": wid, "c": chunk_id})
                            spans = [span_dict(r) for r in await cur.fetchall()]
    except (psycopg.Error, RuntimeError):
        return JSONResponse(status_code=503, content={"error_code": "EVIDENCE_UNAVAILABLE",
                                                      "message": "evidence backend unavailable", "retryable": True})
    return open_payload(view, version, spans)
