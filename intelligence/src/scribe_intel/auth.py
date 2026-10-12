from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException

from .config import Settings, get_settings

REQUIRED_CLAIMS = ["exp", "sub", "wid", "scope", "aud"]


@dataclass(frozen=True)
class Principal:
    sub: str
    wid: str
    scope: tuple[str, ...]


def verify_token(token: str, settings: Settings) -> Principal:
    """Verify an internal HS256 token. Raises jwt.PyJWTError on any failure."""
    if not settings.internal_service_secret:
        raise jwt.InvalidTokenError("internal secret not configured")
    claims = jwt.decode(
        token,
        settings.internal_service_secret,
        algorithms=["HS256"],
        audience=settings.token_audience,
        options={"require": REQUIRED_CLAIMS},
    )
    scope = claims["scope"]
    scopes = tuple(scope.split()) if isinstance(scope, str) else tuple(scope)
    return Principal(sub=str(claims["sub"]), wid=str(claims["wid"]), scope=scopes)


def require_scope(needed: str):
    def dependency(
        authorization: str | None = Header(default=None),
        settings: Settings = Depends(get_settings),
    ) -> Principal:
        unauthorized = HTTPException(
            status_code=401,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
        if not authorization or not authorization.lower().startswith("bearer "):
            raise unauthorized
        try:
            principal = verify_token(authorization[7:].strip(), settings)
        except jwt.PyJWTError:
            raise unauthorized from None
        if needed not in principal.scope:
            raise HTTPException(status_code=403, detail="insufficient scope")
        return principal

    return dependency
