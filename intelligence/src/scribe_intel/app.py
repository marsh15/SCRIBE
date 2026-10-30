from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from .auth import Principal, require_scope
from .compare_routes import router as compare_router
from .db import close_pool, get_pool
from .evidence_routes import router as evidence_router
from .ingest_routes import router as ingest_router
from .retrieve_routes import router as retrieve_router
from .verify_routes import router as verify_router
from .trace_routes import router as trace_router

router = APIRouter()


@router.get("/health")
async def health(_: Principal = Depends(require_scope("health"))) -> dict:
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            cur = await conn.execute("select version()")
            pg = (await cur.fetchone())[0]
            cur = await conn.execute(
                "select extversion from pg_extension where extname = 'vector'"
            )
            row = await cur.fetchone()
    except Exception as exc:  # noqa: BLE001 - surface as 503 without leaking details
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"ok": True, "postgres_version": pg, "pgvector_version": row[0] if row else None}


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_pool()


def create_app() -> FastAPI:
    app = FastAPI(title="scribe-intel", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    # Vercel rewrites may or may not strip the /api/py prefix; serve both.
    app.include_router(router)
    app.include_router(router, prefix="/api/py", include_in_schema=False)
    app.include_router(ingest_router)
    app.include_router(ingest_router, prefix="/api/py", include_in_schema=False)
    app.include_router(retrieve_router)
    app.include_router(retrieve_router, prefix="/api/py", include_in_schema=False)
    app.include_router(compare_router)
    app.include_router(compare_router, prefix="/api/py", include_in_schema=False)
    app.include_router(evidence_router)
    app.include_router(evidence_router, prefix="/api/py", include_in_schema=False)
    app.include_router(trace_router)
    app.include_router(trace_router, prefix="/api/py", include_in_schema=False)
    app.include_router(verify_router)
    app.include_router(verify_router, prefix="/api/py", include_in_schema=False)
    return app


app = create_app()
