from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from cbt_service.database.database import SessionDep
from cbt_service.schemas.item_subject_schemas import (
    ItemCreate, ItemStatusUpdate, ItemUpdate, ItemRead, ItemUsageRead, BulkUploadResult, CurrentUser, PaginatedResponse
)
from cbt_service.services.item.item_service import ItemService
from cbt_service.services.item.bulk_upload_service import BulkUploadService, read_upload_within_limit
from cbt_service.services.difficulty_service import DifficultyScope
from cbt_service.database.models.enums.item_subject_enums import ItemStatus, ItemType
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(prefix="/subjects/{subject_id}/items", tags=["questions items"])



''' CREATE ITEM ➕ '''
@router.post("", response_model=ItemRead)
def create_item(
    subject_id: UUID,
    payload: ItemCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
):
    item = ItemService.create(session, subject_id, payload, current_user)
    return ItemService.to_reads(session, [item])[0]


''' LIST ITEMS 📋 '''
@router.get("", response_model=PaginatedResponse[ItemRead])
def list_items(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
    status: ItemStatus | None = Query(default=None),
    difficulty: str | None = Query(default=None, max_length=40, description="Difficulty level key"),
    type: ItemType | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    bank_id: UUID | None = Query(default=None, description="Only questions in this bank"),
    no_bank: bool = Query(default=False, description="Only questions not in any bank"),
    topic_id: UUID | None = Query(default=None, description="Only questions tagged with this topic"),
    period_id: UUID | None = Query(default=None, description="Only questions for this period (a session includes its terms)"),
    no_period: bool = Query(default=False, description="Only questions without a period"),
):
    items, total = ItemService.get_all(
        session, subject_id, current_user,
        status=status, difficulty=difficulty,
        item_type=type, search=search,
        page=page, page_size=page_size,
        bank_id=bank_id, no_bank=no_bank, topic_id=topic_id,
        period_id=period_id, no_period=no_period,
    )
    return PaginatedResponse(
        items=ItemService.to_reads(session, list(items)),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


''' GET ITEM BY ID 🔍 '''
@router.get("/{item_id}", response_model=ItemRead)
def get_item(
    subject_id: UUID,
    item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
):
    item = ItemService.get_by_id(session, item_id, current_user)
    return ItemService.to_reads(session, [item])[0]


''' UPDATE ITEM ✏️ '''
@router.patch("/{item_id}", response_model=ItemRead)
def update_item(
    subject_id: UUID,
    item_id: UUID,
    payload: ItemUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
):
    item = ItemService.update(session, item_id, payload, current_user)
    return ItemService.to_reads(session, [item])[0]


''' UPDATE ITEM STATUS 🔄 '''
@router.patch("/{item_id}/status", response_model=ItemRead)
def update_item_status(
    subject_id: UUID,
    item_id: UUID,
    payload: ItemStatusUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
):
    item = ItemService.update_status(session, item_id, payload.status, current_user)
    return ItemService.to_reads(session, [item])[0]


''' ITEM USAGE (CAN IT BE DELETED?) 🔎 '''
@router.get("/{item_id}/usage", response_model=ItemUsageRead)
def item_usage(
    subject_id: UUID,
    item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
) -> ItemUsageRead:
    """How many times students answered it and which exams it's on."""
    return ItemService.usage(session, item_id, current_user)


''' DELETE ITEM PERMANENTLY ❌ '''
@router.delete("/{item_id}", status_code=204)
def delete_item(
    subject_id: UUID,
    item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
) -> None:
    """
    Only when no student has answered it and it isn't on an exam past draft
    (it's taken off draft exams). Otherwise 409: archive it via PATCH .../status.
    """
    ItemService.delete(session, item_id, current_user)


''' BULK UPLOAD TEMPLATE ⬇️ '''
@router.get("/bulk/template", tags=["bulk-upload"])
def download_template(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_VIEW)),
):
    """Download the Excel template for bulk question upload."""
    levels = DifficultyScope.levels(session, current_user.org_id)
    file_bytes = BulkUploadService.generate_template([l.name for l in levels if l.status == "active"])
    return Response(
        content=file_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=questions_template.xlsx"},
    )


''' BULK UPLOAD ⬆️ '''
@router.post("/bulk", response_model=BulkUploadResult, tags=["bulk-upload"])
async def bulk_upload_items(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.QUESTIONS_MANAGE)),
    file: UploadFile = File(...),
    bank_id: UUID | None = Form(default=None),
    period_id: UUID | None = Form(default=None),
):
    """
    Upload questions in bulk via Excel file. Optionally put every question in
    the file into one bank (bank_id) and/or period (period_id); an optional
    "topics" column tags each row. Accepts the native template
    or a supported third-party export (currently Edu Expression).
    Use GET /bulk/template to download the native format.
    Partial success is supported: valid rows are saved even if some rows fail.
    Max file size: 5MB.
    """
    if not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are accepted.")

    file_bytes = await read_upload_within_limit(file)
    return await run_in_threadpool(
        BulkUploadService.process_upload,
        session=session,
        file_bytes=file_bytes,
        filename=file.filename,
        subject_id=subject_id,
        current_user=current_user,
        bank_id=bank_id,
        period_id=period_id,
    )

# ''' BULK UPLOAD ⬆️ '''
# @router.post("/bulk", response_model=BulkUploadResult, tags=["bulk-upload"])
# async def bulk_upload_items(
#     subject_id: UUID,
#     session: SessionDep,
#     current_user: CurrentUser = Depends(TeacherOrAbove),
#     file: UploadFile = File(...),
# ):
#     """
#     Upload questions in bulk via Excel file.
#     Use GET /bulk/template to download the expected format.
#     Partial success is supported — valid rows are saved even if some rows fail.
#     """
#     if not file.filename.endswith((".xlsx", ".xls")):
#         from fastapi import HTTPException
#         raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are accepted.")

#     file_bytes = await file.read()
#     result = BulkUploadService.process_upload(
#         session=session,
#         file_bytes=file_bytes,
#         filename=file.filename,
#         subject_id=subject_id,
#         current_user=current_user,
#     )
#     return result
