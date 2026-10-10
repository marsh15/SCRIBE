import os
import time

import jwt
import pytest

SECRET = "test-secret-test-secret-test-secret-32b"
os.environ["INTERNAL_SERVICE_SECRET"] = SECRET
# The app reads DATABASE_URL; tests that need a real DB opt in via DATABASE_URL_TEST.
os.environ.pop("NEON_DATABASE_URL", None)
os.environ.pop("DATABASE_URL", None)

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
