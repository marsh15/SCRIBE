"""Authenticated download of a private blob, with a size cap."""

from __future__ import annotations

import httpx

DEFAULT_MAX_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


class BlobDownloadError(Exception):
    """The blob could not be fetched. `retryable` is True for 5xx/429/network errors."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


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
    The token is never included in error messages.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        with httpx.Client(timeout=timeout, transport=transport, follow_redirects=True) as client:
            with client.stream("GET", url, headers=headers) as resp:
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
