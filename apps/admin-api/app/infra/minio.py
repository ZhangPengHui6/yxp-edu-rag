"""Object storage: MinIO presigned URLs in production, local signed URLs in dev."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import time
from pathlib import Path
from typing import Protocol

from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.shared.ids import new_id
from app.shared.time import utcnow

_UNSAFE = re.compile(r"[^0-9A-Za-z._\-一-鿿]+")


def _safe_filename(filename: str) -> str:
    name = os.path.basename(filename or "upload.bin")
    cleaned = _UNSAFE.sub("_", name).strip("._") or "upload.bin"
    return cleaned[:120]


def build_object_key(filename: str) -> str:
    day = utcnow().strftime("%Y/%m/%d")
    return f"uploads/{day}/{new_id('obj')}/{_safe_filename(filename)}"


def _sign(key: str, expires_at: int) -> str:
    payload = f"{key}:{expires_at}".encode()
    digest = hmac.new(settings.jwt_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


class ObjectStorage(Protocol):
    async def presign_put(self, filename: str) -> dict:
        ...

    async def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        ...

    async def get_bytes(self, key: str) -> bytes:
        ...

    async def health(self) -> dict:
        ...


class LocalStorage:
    """Dev-only signed upload URLs served by the API itself."""

    def __init__(self) -> None:
        self.root = settings.upload_path
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        target = (self.root / key).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise ValueError("invalid object key")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def verify(self, key: str, expires_at: int, signature: str) -> bool:
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(_sign(key, expires_at), signature)

    async def presign_put(self, filename: str) -> dict:
        key = build_object_key(filename)
        expires_at = int(time.time()) + settings.upload_url_ttl
        url = (
            f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}"
            f"/_dev/uploads/{key}?expires={expires_at}&sig={_sign(key, expires_at)}"
        )
        return {"uploadUrl": url, "objectKey": key, "expiresIn": settings.upload_url_ttl}

    async def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        path = self._path(key)
        await run_in_threadpool(path.write_bytes, data)

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(f"对象不存在：{key}")
        return await run_in_threadpool(path.read_bytes)

    async def health(self) -> dict:
        return {"backend": "local", "ok": self.root.exists(), "detail": str(self.root)}


class MinioStorage:
    def __init__(self) -> None:
        from minio import Minio

        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(settings.minio_bucket):
            self.client.make_bucket(settings.minio_bucket)

    async def presign_put(self, filename: str) -> dict:
        from datetime import timedelta

        key = build_object_key(filename)

        def _presign() -> str:
            self._ensure_bucket()
            return self.client.presigned_put_object(
                settings.minio_bucket, key, expires=timedelta(seconds=settings.upload_url_ttl)
            )

        url = await run_in_threadpool(_presign)
        return {"uploadUrl": url, "objectKey": key, "expiresIn": settings.upload_url_ttl}

    async def put_bytes(self, key: str, data: bytes, content_type: str = "") -> None:
        import io

        def _put() -> None:
            self._ensure_bucket()
            self.client.put_object(
                settings.minio_bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type or "application/octet-stream",
            )

        await run_in_threadpool(_put)

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            response = self.client.get_object(settings.minio_bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await run_in_threadpool(_get)

    async def health(self) -> dict:
        def _check() -> bool:
            return self.client.bucket_exists(settings.minio_bucket)

        try:
            exists = await run_in_threadpool(_check)
            return {"backend": "minio", "ok": exists, "detail": f"bucket={exists}"}
        except Exception as exc:  # noqa: BLE001
            return {"backend": "minio", "ok": False, "detail": str(exc)}


_storage: ObjectStorage | None = None


def get_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = MinioStorage() if settings.object_storage == "minio" else LocalStorage()
    return _storage
