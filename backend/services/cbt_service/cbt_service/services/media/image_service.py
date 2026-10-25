"""Validates and stores images uploaded from the question editor."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException

from cbt_service.core.storage.file_storage import FileStorage
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.schemas.media_schemas import ImageUploadRead

# Raster formats only. SVG is deliberately excluded: it can carry scripts.
ALLOWED_IMAGE_TYPES: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}


def sniff_image_type(data: bytes) -> str | None:
    """Content type from the file's magic bytes, or None if not an allowed image."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def org_media_prefix(org_id: UUID) -> str:
    """
    Everything one organisation stores lives under this prefix
    (images today; logos, exports, etc. later), so deleting, exporting,
    measuring or moving a school's media is a single prefix operation.
    Uses the org ID, not its name: names change and IDs don't.
    """
    return f"orgs/{org_id}/"


def build_image_key(org_id: UUID, extension: str, now: datetime | None = None) -> str:
    """orgs/{org_id}/images/{yyyy}/{mm}/{random}.{ext}: per-school, unguessable, never reused."""
    moment: datetime = now or datetime.now(timezone.utc)
    return f"{org_media_prefix(org_id)}images/{moment:%Y/%m}/{uuid4().hex}.{extension}"


class ImageService:

    @staticmethod
    def store(data: bytes, current_user: CurrentUser, storage: FileStorage) -> ImageUploadRead:
        if not data:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        content_type: str | None = sniff_image_type(data)
        if content_type is None:
            raise HTTPException(
                status_code=415,
                detail="Unsupported image. Upload a PNG, JPEG, GIF or WebP file.",
            )

        key: str = build_image_key(current_user.org_id, ALLOWED_IMAGE_TYPES[content_type])
        try:
            url: str = storage.save(key, data, content_type)
        except HTTPException:
            raise
        except Exception as exc:  # storage outage / misconfiguration
            raise HTTPException(
                status_code=502, detail="Could not store the image. Please try again."
            ) from exc

        return ImageUploadRead(url=url, key=key, content_type=content_type, size=len(data))
