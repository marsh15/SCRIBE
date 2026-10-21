"""Serve the review app. Binds 127.0.0.1 only; uvicorn is imported here, lazily, so nothing else imports it."""

from __future__ import annotations

from fastapi import FastAPI

LOCAL_HOST = "127.0.0.1"


def check_host(host: str) -> None:
    if host != LOCAL_HOST:
        raise ValueError(f"refusing to bind {host!r}: the review tool serves only on {LOCAL_HOST}")


def serve(app: FastAPI, port: int = 8765, host: str = LOCAL_HOST) -> None:
    check_host(host)
    import uvicorn

    uvicorn.run(app, host=LOCAL_HOST, port=port, log_level="warning")
