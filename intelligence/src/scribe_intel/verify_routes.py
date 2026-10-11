"""POST /verify and POST /verify/read (T26). Scope `verify`; the workspace comes from the token only (INV-15).

/verify: ``{message_id?, bundles:[{bundle_id, items:[{label, item_id?, source_version_id?}]}], claims:[{index, text}]}``.
A bundle or message outside the caller's workspace answers 404 (indistinguishable from an unknown id). The response
carries per-claim statuses and never any quote text. /verify/read returns the stored rows for message ids.
Provider problems never fail the request: the claims come back UNVERIFIED (INV-9). Database problems answer 503.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import verification as v
from .auth import Principal, require_scope
from .db import get_pool

router = APIRouter()
MAX_REQUEST_CLAIMS = 200


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str = Field(pattern=r"^E\d{1,6}$")
    item_id: uuid.UUID | None = None
    source_version_id: uuid.UUID | None = None


class BundleBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bundle_id: uuid.UUID
    items: list[ItemBody] = Field(default_factory=list, max_length=500)


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=4000)


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    message_id: str | None = Field(default=None, min_length=1, max_length=200)
    bundles: list[BundleBody] = Field(default_factory=list, max_length=50)
    claims: list[ClaimBody] = Field(max_length=MAX_REQUEST_CLAIMS)


class ReadBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message_ids: list[str] = Field(max_length=200)


def _err(status: int, code: str, message: str, retryable: bool) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error_code": code, "message": message, "retryable": retryable})


def _wid_or_error(principal: Principal) -> JSONResponse | None:
    try:
        uuid.UUID(principal.wid)
    except ValueError:
        return _err(403, "INVALID_WORKSPACE", "invalid workspace", False)
    return None


@router.post("/verify")
async def verify(body: VerifyBody, principal: Principal = Depends(require_scope("verify"))) -> Any:
    if (bad := _wid_or_error(principal)) is not None:
        return bad
    bundles = [v.DeclaredBundle(b.bundle_id, tuple(v.DeclaredItem(i.label, i.item_id, i.source_version_id) for i in b.items))
               for b in body.bundles]
    claims = [v.ClaimIn(c.index, c.text) for c in body.claims]
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            outcome = await v.verify_claims(conn, principal.wid, bundles=bundles, claims=claims,
                                            message_id=body.message_id, verifier=v.make_verifier())
    except v.NotFound:
        return _err(404, "NOT_FOUND", "not found", False)
    except (psycopg.Error, RuntimeError):
        return _err(503, "VERIFY_UNAVAILABLE", "verification backend unavailable", True)
    return {"message_id": body.message_id, **outcome.as_json()}


@router.post("/verify/read")
async def verify_read(body: ReadBody, principal: Principal = Depends(require_scope("verify"))) -> Any:
    if (bad := _wid_or_error(principal)) is not None:
        return bad
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            rows = await v.read_verifications(conn, principal.wid, body.message_ids)
    except (psycopg.Error, RuntimeError):
        return _err(503, "VERIFY_UNAVAILABLE", "verification backend unavailable", True)
    return {"messages": rows}
