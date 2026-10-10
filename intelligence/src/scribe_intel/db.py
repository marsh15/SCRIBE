from psycopg_pool import AsyncConnectionPool

from .config import get_settings

_pool: AsyncConnectionPool | None = None


async def get_pool() -> AsyncConnectionPool:
    """Lazily open a small pool. prepare_threshold=None disables prepared
    statements, required behind Neon's PgBouncer pooler."""
    global _pool
    if _pool is None:
        dsn = get_settings().dsn
        if not dsn:
            raise RuntimeError("DATABASE_URL / NEON_DATABASE_URL is not configured")
        pool = AsyncConnectionPool(
            dsn,
            min_size=0,
            max_size=3,
            open=False,
            kwargs={"prepare_threshold": None, "autocommit": True},
        )
        await pool.open()
        _pool = pool
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
