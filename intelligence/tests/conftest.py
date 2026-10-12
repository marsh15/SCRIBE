import os
import time

import jwt
import pytest

SECRET = "test-secret-test-secret-test-secret-32b"
os.environ["INTERNAL_SERVICE_SECRET"] = SECRET
# The app reads DATABASE_URL; tests that need a real DB opt in via DATABASE_URL_TEST.
os.environ.pop("NEON_DATABASE_URL", None)
os.environ.pop("DATABASE_URL", None)
os.environ.pop("VOYAGE_API_KEY", None)  # no test may ever reach Voyage live

DATABASE_URL_TEST = os.environ.get("DATABASE_URL_TEST")
needs_db = pytest.mark.skipif(
    not DATABASE_URL_TEST,
    reason="DATABASE_URL_TEST not set (run `docker compose up -d db` and export "
    "DATABASE_URL_TEST=postgresql://scribe:scribe@localhost:54329/scribe)",
)


def make_token(
    *,
    secret: str = SECRET,
    aud: str | None = "scribe-intel",
    exp_in: int = 60,
    scope: str | None = "health",
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims = {
        "sub": "user_1",
        "wid": "ws_1",
        "scope": scope,
        "aud": aud,
        "iat": now,
        "exp": now + exp_in,
    }
    for key in drop:
        claims.pop(key, None)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret, algorithm="HS256")


# --- committed-data hygiene -------------------------------------------------------------------
# Many DB tests commit real rows (workspaces, files, chunks, embeddings, retrieval traces) through
# autocommit connections or the app's own pool. Left behind, they grow the shared test DB until the
# HNSW approximate-NN result drifts from the exact result (test_dense). These autouse fixtures delete
# every workspace created during a module (and everything scoped to it) when the module ends, and
# anything still left (the cross-module corpus cache) when the session ends. Seeded profile tables,
# extensions and schema are never touched.
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent / "integration"))


@pytest.fixture(scope="session", autouse=True)
def _purge_session_workspaces():
    if not DATABASE_URL_TEST:
        yield
        return
    from _db import purge_workspaces, workspace_ids

    before = workspace_ids(DATABASE_URL_TEST)
    yield
    purge_workspaces(DATABASE_URL_TEST, workspace_ids(DATABASE_URL_TEST) - before)


@pytest.fixture(scope="module", autouse=True)
def _purge_module_workspaces():
    if not DATABASE_URL_TEST:
        yield
        return
    from _db import PROTECTED_WORKSPACES, purge_workspaces, workspace_ids

    before = workspace_ids(DATABASE_URL_TEST)
    yield
    purge_workspaces(DATABASE_URL_TEST, workspace_ids(DATABASE_URL_TEST) - before - PROTECTED_WORKSPACES)
