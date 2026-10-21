"""The review web app (FastAPI). Local only: see ``server.serve`` for the 127.0.0.1 guard."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .kinds import ADAPTERS, REVIEW_KEYS
from .page import PAGE_HTML
from .source import Corpus
from .store import ItemStore, ReviewError, default_order, ids_from_order_file

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class Decision(BaseModel):
    note: str | None = None
    reason: str | None = None
    values: dict[str, Any] | None = None


def build_order(items_path: Path, order_file: str | Path | None) -> list[str]:
    if order_file:
        return ids_from_order_file(Path(order_file))
    return default_order(items_path)


def create_app(items_path: str | Path, kind: str, corpus_dir: str | Path, reviewer: str,
               order: Iterable[str] | None = None, allowed_hosts: Iterable[str] = LOCAL_HOSTS) -> FastAPI:
    if kind not in ADAPTERS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {', '.join(ADAPTERS)}")
    corpus = Corpus(corpus_dir)
    if not corpus.dir.is_dir():
        raise ValueError(f"corpus directory not found: {corpus_dir}")
    adapter = ADAPTERS[kind]
    items_path = Path(items_path)
    ordered = list(order) if order is not None else default_order(items_path)
    store = ItemStore(items_path, adapter, corpus, reviewer, ordered)
    hosts = frozenset(allowed_hosts)

    app = FastAPI(title="scribe-eval review", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.middleware("http")
    async def local_only(request: Request, call_next):  # DNS-rebinding / cross-site guard
        host = urlsplit("//" + (request.headers.get("host") or "")).hostname or ""
        if host not in hosts:
            return JSONResponse({"errors": [f"host {host!r} not allowed"]}, status_code=403)
        origin = request.headers.get("origin")
        if origin and (urlsplit(origin).hostname or "") not in hosts:
            return JSONResponse({"errors": ["cross-origin request refused"]}, status_code=403)
        return await call_next(request)

    @app.exception_handler(ReviewError)
    async def review_error(_: Request, exc: ReviewError):
        return JSONResponse({"errors": exc.errors}, status_code=exc.status)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return PAGE_HTML.replace("__KIND__", kind).replace("__REVIEWER__", _esc(reviewer))

    @app.get("/api/state")
    def state() -> dict[str, Any]:
        return {**store.state(), "kind": kind, "reviewer": reviewer}

    @app.get("/api/nav")
    def nav(frm: str | None = None, direction: str = "next", pending: bool = True) -> dict[str, Any]:
        return {"id": store.nav(frm, "prev" if direction == "prev" else "next", pending)}

    @app.get("/api/items/{item_id}")
    def item(item_id: str) -> dict[str, Any]:
        raw = store.get(item_id)
        seq = store.sequence()
        shown = [{"key": k, "value": v} for k, v in raw.items() if k != "evidence" and k not in REVIEW_KEYS]
        review = {k: raw.get(k) for k in REVIEW_KEYS if raw.get(k) is not None}
        sources = []
        for loc in adapter.locations(raw):
            view = corpus.view(loc.doc, loc.page, loc.needle, needle_label=loc.needle_label).to_json()
            sources.append({"label": loc.label, "meta": loc.meta, "doc": loc.doc, "needle": loc.needle, **view})
        return {
            "id": item_id, "position": seq.index(item_id) + 1 if item_id in seq else None, "count": len(seq),
            "reviewed": store.is_reviewed(raw), "fields": shown, "review": review, "raw": raw,
            "sources": sources, "editor": adapter.editor(),
            "problems": adapter.validate(raw, corpus),
        }

    @app.get("/api/page.png")
    def page_png(doc: str, page: int) -> Response:
        data = corpus.png(doc, page)
        if data is None:
            return JSONResponse({"errors": ["no such PDF page"]}, status_code=404)
        return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})

    def after(item_id: str) -> dict[str, Any]:
        return {"ok": True, "next": store.nav(item_id, "next"), "state": store.state()}

    @app.post("/api/items/{item_id}/accept")
    def accept(item_id: str, body: Decision) -> dict[str, Any]:
        store.accept(item_id, body.note)
        return after(item_id)

    @app.post("/api/items/{item_id}/edit")
    def edit(item_id: str, body: Decision) -> dict[str, Any]:
        store.edit(item_id, body.values or {}, body.note)
        return after(item_id)

    @app.post("/api/items/{item_id}/drop")
    def drop(item_id: str, body: Decision) -> dict[str, Any]:
        nxt = store.nav(item_id, "next")
        store.drop(item_id, body.reason or "")
        return {"ok": True, "next": None if nxt == item_id else nxt, "state": store.state()}

    return app


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
