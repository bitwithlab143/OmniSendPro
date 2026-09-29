"""S3-compatible object storage through presigned URLs only (design DS-21, ADR-015).

boto3 is used purely to *sign* URLs; it never opens a connection. Browsers upload with a presigned POST
(size-limited by the policy) and the import worker downloads with a presigned GET streamed through httpx,
so the API never carries file bytes.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import bad_request

UPLOAD_URL_TTL = 15 * 60
DOWNLOAD_URL_TTL = 60 * 60


def enabled() -> bool:
    return get_settings().object_storage_enabled


def _client(public: bool = False) -> Any:
    import boto3
    from botocore.config import Config

    s: Settings = get_settings()
    if not s.object_storage_enabled:
        raise bad_request("Object storage is not configured", "object_storage_disabled")
    endpoint = (s.object_storage_public_endpoint if public else None) or s.object_storage_endpoint or None
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=s.object_storage_region,
        aws_access_key_id=s.object_storage_access_key,
        aws_secret_access_key=s.object_storage_secret_key,
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path" if s.object_storage_path_style else "virtual"}),
    )


def presign_upload(key: str, max_bytes: int | None = None) -> dict[str, Any]:
    s = get_settings()
    limit = max_bytes or s.object_storage_max_bytes
    post = _client(public=True).generate_presigned_post(
        Bucket=s.object_storage_bucket,
        Key=key,
        Conditions=[["content-length-range", 1, limit]],
        ExpiresIn=UPLOAD_URL_TTL,
    )
    return {"url": post["url"], "fields": post["fields"], "object_key": key, "max_bytes": limit,
            "expires_in": UPLOAD_URL_TTL}


def presign_get(key: str) -> str:
    return str(_client().generate_presigned_url("get_object", Params={"Bucket": get_settings().object_storage_bucket,
                                                                      "Key": key}, ExpiresIn=DOWNLOAD_URL_TTL))


def delete(key: str) -> None:
    url = _client().generate_presigned_url("delete_object", Params={"Bucket": get_settings().object_storage_bucket,
                                                                   "Key": key}, ExpiresIn=300)
    httpx.delete(url, timeout=30).raise_for_status()


class TooLarge(Exception):
    pass


class HttpObjectReader(io.RawIOBase):
    """Blocking, streaming file object over a presigned GET (used from the parser's worker thread)."""

    def __init__(self, url: str, max_bytes: int) -> None:
        self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0))
        self._response = self._client.send(self._client.build_request("GET", url), stream=True)
        if self._response.status_code == 404:
            self.close()
            raise FileNotFoundError("The uploaded file was not found (expired or never uploaded)")
        self._response.raise_for_status()
        self._chunks: Iterator[bytes] = self._response.iter_bytes(64 * 1024)
        self._pending = b""
        self._read = 0
        self._max = max_bytes

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        if not self._pending:
            self._pending = next(self._chunks, b"")
            self._read += len(self._pending)
            if self._read > self._max:
                raise TooLarge(f"File exceeds {self._max} bytes")
        n = min(len(buffer), len(self._pending))
        buffer[:n] = self._pending[:n]
        self._pending = self._pending[n:]
        return n

    @property
    def bytes_read(self) -> int:
        return self._read

    def close(self) -> None:
        if not self.closed:
            if hasattr(self, "_response"):
                self._response.close()
            self._client.close()
        super().close()


def open_object(key: str) -> HttpObjectReader:
    return HttpObjectReader(presign_get(key), get_settings().object_storage_max_bytes)
