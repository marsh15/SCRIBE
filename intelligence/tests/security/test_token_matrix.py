"""T27: every Python endpoint rejects missing, malformed, expired, wrong-audience, wrong-signature, wrong-scope and
claim-less tokens (INV-15). The route table is checked against the FastAPI app itself: a new route that is not listed
here, or one that has no `require_scope` dependency, fails the guard test. No database is needed: authentication runs
before any handler code, and a valid token with an empty body is stopped by request validation (422) before the DB."""
from __future__ import annotations

import inspect
import time
import uuid

import httpx
import jwt
import pytest
from conftest import SECRET
from fastapi.routing import APIRoute

from scribe_intel.app import create_app

# method, path, required scope, whether the route takes a JSON body (a valid token + {} then answers 422)
ROUTES = [
    ("GET", "/health", "health", False),
    ("POST", "/ingest/source", "ingest", True),
    ("POST", "/ingest/step", "ingest_step", False),
    ("POST", "/ingest/sweep", "ingest_step", False),
    ("POST", "/ingest/purge", "ingest_step", False),
    ("POST", "/retrieve", "retrieve", True),
    ("POST", "/compare", "compare", True),
    ("POST", "/evidence/open", "evidence_read", True),
    ("POST", "/retrieval/trace", "retrieval_trace_read", True),
    ("POST", "/verify", "verify", True),
    ("POST", "/verify/read", "verify", True),
]
PREFIXES = ("", "/api/py")
WID = str(uuid.uuid4())


def mint(*, scope="x", aud="scribe-intel", exp_in=60, secret=SECRET, drop=(), alg="HS256", extra=None) -> str:
    now = int(time.time())
    claims = {"sub": "user_1", "wid": WID, "scope": scope, "aud": aud, "iat": now, "exp": now + exp_in, **(extra or {})}
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, None if alg == "none" else secret, algorithm=alg)


@pytest.fixture(scope="module")
def app():
    return create_app()


@pytest.fixture()
async def client(app):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def call(client, method, path, headers=None, has_body=False):
    kwargs = {"headers": headers or {}}
    if method == "POST":
        kwargs["json"] = {}  # empty on purpose: the request must never get past auth and validation
    return await client.request(method, path, **kwargs)


def bearer(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


def iter_routes(app):
    """(full path, APIRoute) for every endpoint, whichever way this FastAPI version nests included routers."""
    for r in app.routes:
        if isinstance(r, APIRoute):
            yield r.path, r
        elif hasattr(r, "original_router"):  # fastapi >= 0.14x wraps include_router() results
            prefix = r.include_context.prefix
            for inner in r.original_router.routes:
                if isinstance(inner, APIRoute):
                    yield prefix + inner.path, inner


def required_scope(route: APIRoute) -> str | None:
    """The scope a route's `require_scope(...)` dependency demands, read from the dependency's closure."""
    found = []
    for dep in route.dependant.dependencies:
        fn = dep.call
        if getattr(fn, "__qualname__", "").startswith("require_scope."):
            found.append(inspect.getclosurevars(fn).nonlocals["needed"])
    return found[0] if len(found) == 1 else None


def test_route_table_matches_the_app_exactly(app):
    """Guard: a new endpoint must be added to ROUTES (and so to every test below) and must be scope-protected."""
    live = {}
    for path, r in iter_routes(app):
        for m in r.methods - {"HEAD", "OPTIONS"}:
            live[(m, path)] = r
    expected = {(m, p) for m, p, _, _ in ROUTES}
    expected |= {(m, "/api/py" + p) for m, p, _, _ in ROUTES}
    assert set(live) == expected, f"route table out of date; unlisted={set(live) - expected} gone={expected - set(live)}"
    for m, p, scope, _ in ROUTES:
        for prefix in PREFIXES:
            assert required_scope(live[(m, prefix + p)]) == scope, (m, prefix + p)


def test_no_request_model_declares_a_workspace_field(app):
    """INV-15: the workspace is never an input. No body/query/path parameter may be called workspace_id/wid/..."""
    banned = {"workspace_id", "workspaceid", "wid", "workspace", "tenant_id", "user_id", "userid"}
    for _, r in iter_routes(app):
        names = {p.name.lower() for p in r.dependant.query_params + r.dependant.path_params + r.dependant.header_params}
        assert not (names & banned), (names,)
        for f in r.dependant.body_params:
            model = f.field_info.annotation if hasattr(f, "field_info") else None
            fields = getattr(model, "model_fields", {})
            assert not ({k.lower() for k in fields} & banned), (set(fields) & banned,)


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method,path,scope,has_body", ROUTES)
class TestRejections:
    async def test_missing_token(self, client, prefix, method, path, scope, has_body):
        assert (await call(client, method, prefix + path)).status_code == 401

    @pytest.mark.parametrize("header", ["", "Bearer", "Bearer ", "Bearer not-a-jwt", "Basic abc", "bearer.x.y.z", "Token abc"])
    async def test_malformed_authorization(self, client, prefix, method, path, scope, has_body, header):
        assert (await call(client, method, prefix + path, {"authorization": header})).status_code == 401

    async def test_expired(self, client, prefix, method, path, scope, has_body):
        r = await call(client, method, prefix + path, bearer(mint(scope=scope, exp_in=-120)))
        assert r.status_code == 401

    @pytest.mark.parametrize("aud", ["someone-else", "scribe-intel-admin", "", "SCRIBE-INTEL"])
    async def test_wrong_audience(self, client, prefix, method, path, scope, has_body, aud):
        assert (await call(client, method, prefix + path, bearer(mint(scope=scope, aud=aud)))).status_code == 401

    async def test_wrong_signature(self, client, prefix, method, path, scope, has_body):
        r = await call(client, method, prefix + path, bearer(mint(scope=scope, secret="y" * 40)))
        assert r.status_code == 401

    async def test_alg_none(self, client, prefix, method, path, scope, has_body):
        assert (await call(client, method, prefix + path, bearer(mint(scope=scope, alg="none")))).status_code == 401

    async def test_other_hmac_algorithm_is_not_accepted(self, client, prefix, method, path, scope, has_body):
        assert (await call(client, method, prefix + path, bearer(mint(scope=scope, alg="HS512")))).status_code == 401

    @pytest.mark.parametrize("drop", ["sub", "wid", "scope", "exp", "aud"])
    async def test_missing_claim(self, client, prefix, method, path, scope, has_body, drop):
        assert (await call(client, method, prefix + path, bearer(mint(scope=scope, drop=(drop,))))).status_code == 401

    async def test_wrong_scope_is_forbidden(self, client, prefix, method, path, scope, has_body):
        others = sorted({s for _, _, s, _ in ROUTES} - {scope})
        for other in others:
            r = await call(client, method, prefix + path, bearer(mint(scope=other)))
            assert r.status_code == 403, (other, r.status_code)

    @pytest.mark.parametrize("variant", ["{s}x", "x{s}", "{s}_admin", "{s}:write", "*", "all", ""])
    async def test_scope_must_match_exactly(self, client, prefix, method, path, scope, has_body, variant):
        token = mint(scope=variant.format(s=scope))
        r = await call(client, method, prefix + path, bearer(token))
        assert r.status_code in (401, 403)  # an empty scope claim is rejected as unauthorized; the rest as forbidden


@pytest.mark.parametrize("prefix", PREFIXES)
@pytest.mark.parametrize("method,path,scope,has_body", [r for r in ROUTES if r[3]])
async def test_a_valid_token_gets_past_auth(client, prefix, method, path, scope, has_body):
    """Positive control for the matrix: the right scope reaches request validation (422 for an empty body), so the
    401/403 results above are about the token and not about a broken route."""
    r = await call(client, method, prefix + path, bearer(mint(scope=scope)))
    assert r.status_code == 422


async def test_space_separated_scope_string_and_list_are_both_split(client):
    for scope in ("retrieve verify", ["retrieve", "verify"]):
        r = await client.post("/retrieve", json={}, headers=bearer(mint(scope=scope)))
        assert r.status_code == 422
    r = await client.post("/ingest/source", json={}, headers=bearer(mint(scope="retrieve verify")))
    assert r.status_code == 403
