import pytest
from conftest import DATABASE_URL_TEST, make_token, needs_db
from fastapi.testclient import TestClient

from scribe_intel import db
from scribe_intel.app import app
from scribe_intel.config import get_settings


def bearer(**kw):
    return {"Authorization": f"Bearer {make_token(**kw)}"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", ["/health", "/api/py/health"])
def test_no_token_401(client, path):
    assert client.get(path).status_code == 401


def test_bad_tokens_401(client):
    assert client.get("/health", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/health", headers=bearer(exp_in=-5)).status_code == 401
    assert client.get("/health", headers=bearer(aud="x")).status_code == 401
    assert client.get("/health", headers=bearer(secret="y" * 40)).status_code == 401


def test_wrong_scope_403(client):
    assert client.get("/health", headers=bearer(scope="other")).status_code == 403


@needs_db
@pytest.mark.parametrize("path", ["/health", "/api/py/health"])
def test_health_200(monkeypatch, path):
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    get_settings.cache_clear()
    db._pool = None
    try:
        with TestClient(app) as client:
            r = client.get(path, headers=bearer())
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert "PostgreSQL" in body["postgres_version"]
            assert body["pgvector_version"]  # pgvector image has the extension
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("path", ["/openapi.json", "/api/py/openapi.json", "/docs", "/redoc"])
def test_no_api_schema_or_docs_are_served(client, path):
    assert client.get(path).status_code == 404
