"""Authenticated download of a private blob, with a size cap."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0
# The bearer token is only ever sent to Vercel Blob (threat-model R11): `files.storage_url` comes from a signed upload
# callback, but a bad row must still not be able to point the token at another host.
ALLOWED_HOST_SUFFIX = ".blob.vercel-storage.com"


class BlobDownloadError(Exception):
    """The blob could not be fetched. `retryable` is True for 5xx/429/network errors."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def assert_allowed_blob_url(url: str) -> None:
    """https only, no userinfo, host exactly `<store>.blob.vercel-storage.com` or `<store>.private.blob...`."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        host, port, parts = "", None, None
    if (parts is None or parts.scheme != "https" or parts.username is not None or parts.password is not None
            or port not in (None, 443) or not host.endswith(ALLOWED_HOST_SUFFIX)
            or len(host) == len(ALLOWED_HOST_SUFFIX)):
        raise BlobDownloadError("Blob URL is not an allowed Vercel Blob address.", retryable=False)


def download_private_blob(
    url: str,
    token: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.BaseTransport | None = None,
) -> bytes:
    """GET `url` with a bearer token and return the body.

    Raises BlobDownloadError on HTTP errors, timeouts, or when the body exceeds
    `max_bytes` (checked against Content-Length first, then while streaming).
    The token is never included in error messages. Only `https://*.blob.vercel-storage.com` is fetched and redirects are
    NOT followed (a redirect would carry the bearer token to another host): a 3xx is an error.
    """
    assert_allowed_blob_url(url)
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=False) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if 300 <= resp.status_code < 400:
                    raise BlobDownloadError(
                        f"Blob download was redirected (HTTP {resp.status_code}); redirects are not followed.",
                        status=resp.status_code,
                    )
                if resp.status_code >= 400:
                    retryable = resp.status_code in (408, 429) or resp.status_code >= 500
                    raise BlobDownloadError(
                        f"Blob download failed with HTTP {resp.status_code}.",
                        status=resp.status_code,
                        retryable=retryable,
                    )
                declared = resp.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise BlobDownloadError(
                        f"Blob is larger than the {max_bytes} byte limit.", status=resp.status_code
                    )
                buf = bytearray()
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise BlobDownloadError(
                            f"Blob is larger than the {max_bytes} byte limit.",
                            status=resp.status_code,
                        )
                return bytes(buf)
    except httpx.TimeoutException as exc:
        raise BlobDownloadError("Blob download timed out.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise BlobDownloadError(
            f"Blob download failed ({type(exc).__name__}).", retryable=True
        ) from exc
