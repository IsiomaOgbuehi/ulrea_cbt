from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.concurrency import run_in_threadpool

from cbt_service.core.settings import settings
from cbt_service.core.storage.file_storage import FileStorage, get_storage
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.schemas.media_schemas import ImageUploadRead
from cbt_service.services.item.bulk_upload_service import read_upload_within_limit
from cbt_service.services.media.image_service import ImageService
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(prefix="/uploads", tags=["uploads"])

# Same roles that can create and edit items.


''' UPLOAD IMAGE 🖼️ '''
@router.post("/images", response_model=ImageUploadRead, status_code=201)
async def upload_image(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
    storage: FileStorage = Depends(get_storage),
) -> ImageUploadRead:
    """
    Upload an image for question / option rich text. Returns the public URL to
    embed. PNG, JPEG, GIF or WebP; max MEDIA_MAX_IMAGE_MB. No DB session is
    taken, so uploads never hold a database connection.
    """
    data: bytes = await read_upload_within_limit(
        file, max_bytes=settings.MEDIA_MAX_IMAGE_MB * 1024 * 1024
    )
    # Storage calls block (disk / boto3), so keep them off the event loop.
    return await run_in_threadpool(ImageService.store, data, current_user, storage)
