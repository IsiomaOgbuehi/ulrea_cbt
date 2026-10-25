"""
Where uploaded files (question/option images) live.

Two interchangeable backends, picked by settings.STORAGE_BACKEND:

- "spaces": DigitalOcean Spaces (S3-compatible object storage + optional CDN).
- "local":  a folder on this server (MEDIA_ROOT), served either by this app at
            MEDIA_URL_PATH or, better in production, directly by nginx.

Both return the public URL that gets embedded in the question HTML. That URL
is built from MEDIA_PUBLIC_BASE_URL, so pointing it at a domain you own
(e.g. https://media.example.com) lets you move between backends later without
rewriting stored content.
"""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from cbt_service.core.settings import settings

if TYPE_CHECKING:  # typing only; boto3-stubs[s3] provides these
    from mypy_boto3_s3.client import S3Client


class FileStorage(ABC):
    """Stores bytes under a key and returns the URL browsers should load."""

    @abstractmethod
    def save(self, key: str, data: bytes, content_type: str) -> str:
        """Store `data` at `key`; return its public URL."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove the object at `key` (no error if it doesn't exist)."""

    @abstractmethod
    def public_url(self, key: str) -> str:
        """Public URL for `key`."""


def _join_url(base_url: str, key: str) -> str:
    return f"{base_url.rstrip('/')}/{key.lstrip('/')}"


class LocalFileStorage(FileStorage):
    """Writes files under `root` on this server's disk."""

    def __init__(self, root: str | Path, base_url: str) -> None:
        self.root: Path = Path(root).resolve()
        self.base_url: str = base_url

    def _path_for(self, key: str) -> Path:
        path: Path = (self.root / key).resolve()
        # Keys are generated server-side, but never let one escape the root.
        if not path.is_relative_to(self.root):
            raise ValueError(f"Storage key escapes media root: {key!r}")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> str:
        path: Path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a reader never sees half a file.
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.chmod(tmp_name, 0o644)  # readable by nginx if it serves the folder
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return self.public_url(key)

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def public_url(self, key: str) -> str:
        return _join_url(self.base_url, key)


class SpacesFileStorage(FileStorage):
    """DigitalOcean Spaces via the S3 API. Objects are public-read."""

    # Keys are unique per upload (uuid), so the content never changes:
    # browsers and the CDN may cache it for a year.
    CACHE_CONTROL: str = "public, max-age=31536000, immutable"

    def __init__(self, client: "S3Client", bucket: str, base_url: str) -> None:
        self.client: "S3Client" = client
        self.bucket: str = bucket
        self.base_url: str = base_url

    def save(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            ACL="public-read",
            CacheControl=self.CACHE_CONTROL,
        )
        return self.public_url(key)

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def public_url(self, key: str) -> str:
        return _join_url(self.base_url, key)


def _build_spaces_storage() -> SpacesFileStorage:
    missing: list[str] = [
        name
        for name in ("SPACES_KEY", "SPACES_SECRET", "SPACES_BUCKET", "SPACES_REGION")
        if not getattr(settings, name)
    ]
    if missing:
        raise RuntimeError(
            f"STORAGE_BACKEND=spaces but these settings are empty: {', '.join(missing)}"
        )

    import boto3  # imported lazily: only needed for the Spaces backend
    from botocore.config import Config

    region: str = settings.SPACES_REGION
    bucket: str = settings.SPACES_BUCKET or ""
    endpoint: str = settings.SPACES_ENDPOINT_URL or f"https://{region}.digitaloceanspaces.com"
    # boto3.client is overloaded per AWS service; only the S3 overload is typed here.
    client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
        "s3",
        region_name=region,
        endpoint_url=endpoint,
        aws_access_key_id=settings.SPACES_KEY,
        aws_secret_access_key=settings.SPACES_SECRET,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )
    # Default to the Spaces CDN edge URL; override with your own domain.
    base_url: str = settings.MEDIA_PUBLIC_BASE_URL or (
        f"https://{bucket}.{region}.cdn.digitaloceanspaces.com"
    )
    return SpacesFileStorage(client=client, bucket=bucket, base_url=base_url)


def _build_local_storage() -> LocalFileStorage:
    base_url: str = settings.MEDIA_PUBLIC_BASE_URL or (
        f"http://localhost:8000{settings.MEDIA_URL_PATH}"
    )
    return LocalFileStorage(root=settings.MEDIA_ROOT, base_url=base_url)


@lru_cache(maxsize=1)
def get_storage() -> FileStorage:
    """FastAPI dependency (and plain function) returning the configured backend."""
    if settings.STORAGE_BACKEND == "spaces":
        return _build_spaces_storage()
    return _build_local_storage()
