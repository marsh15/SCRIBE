import jwt
import pytest
from conftest import SECRET, make_token

from scribe_intel.auth import verify_token
from scribe_intel.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(INTERNAL_SERVICE_SECRET=SECRET)


def test_valid_token(settings):
    p = verify_token(make_token(), settings)
    assert (p.sub, p.wid, p.scope) == ("user_1", "ws_1", ("health",))


def test_expired(settings):
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_token(make_token(exp_in=-10), settings)


def test_wrong_audience(settings):
    with pytest.raises(jwt.InvalidAudienceError):
        verify_token(make_token(aud="someone-else"), settings)


def test_wrong_signature(settings):
    with pytest.raises(jwt.InvalidSignatureError):
        verify_token(make_token(secret="x" * 40), settings)


@pytest.mark.parametrize("claim", ["sub", "wid", "scope", "exp", "aud"])
def test_missing_claim(settings, claim):
    with pytest.raises(jwt.PyJWTError):
        verify_token(make_token(drop=(claim,)), settings)


def test_garbage_token(settings):
    with pytest.raises(jwt.PyJWTError):
        verify_token("not-a-jwt", settings)


def test_alg_none_rejected(settings):
    token = jwt.encode(
        {"sub": "a", "wid": "b", "scope": "health", "aud": "scribe-intel", "exp": 9999999999},
        None,
        algorithm="none",
    )
    with pytest.raises(jwt.PyJWTError):
        verify_token(token, settings)


def test_unconfigured_secret_rejects_everything():
    with pytest.raises(jwt.PyJWTError):
        verify_token(make_token(), Settings(INTERNAL_SERVICE_SECRET=""))
