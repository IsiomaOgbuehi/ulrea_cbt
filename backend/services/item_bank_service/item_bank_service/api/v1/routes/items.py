from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Query
from fastapi.responses import Response
from item_bank_service.database.database import SessionDep
from item_bank_service.dependencies import get_current_user, require_roles
from item_bank_service.schemas.schemas import (
    ItemCreate, ItemUpdate, ItemRead, BulkUploadResult, CurrentUser
)
from item_bank_service.services.item_service import ItemService
from item_bank_service.services.bulk_upload_service import BulkUploadService
from item_bank_service.database.models.enums import UserRole

router = APIRouter(prefix="/subjects/{subject_id}/items", tags=["items"])

TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER)
AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)


''' CREATE ITEM ➕ '''
@router.post("", response_model=ItemRead)
async def create_item(
    subject_id: UUID,
    payload: ItemCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    item = ItemService.create(session, subject_id, payload, current_user)
    return ItemRead.model_validate(item, from_attributes=True)


''' LIST ITEMS 📋 '''
@router.get("", response_model=list[ItemRead])
async def list_items(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
    status: str | None = Query(default=None),
    difficulty: str | None = Query(default=None),
    type: str | None = Query(default=None),
    search: str | None = Query(default=None),
):
    items = ItemService.get_all(
        session, subject_id, current_user,
        status=status, difficulty=difficulty,
        item_type=type, search=search,
    )
    return [ItemRead.model_validate(i, from_attributes=True) for i in items]


''' GET ITEM BY ID 🔍 '''
@router.get("/{item_id}", response_model=ItemRead)
async def get_item(
    subject_id: UUID,
    item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    item = ItemService.get_by_id(session, item_id, current_user)
    return ItemRead.model_validate(item, from_attributes=True)


''' UPDATE ITEM ✏️ '''
@router.patch("/{item_id}", response_model=ItemRead)
async def update_item(
    subject_id: UUID,
    item_id: UUID,
    payload: ItemUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    item = ItemService.update(session, item_id, payload, current_user)
    return ItemRead.model_validate(item, from_attributes=True)


''' DELETE (ARCHIVE) ITEM 🗄️ '''
@router.delete("/{item_id}", status_code=204)
async def delete_item(
    subject_id: UUID,
    item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    ItemService.delete(session, item_id, current_user)


''' BULK UPLOAD TEMPLATE ⬇️ '''
@router.get("/bulk/template", tags=["bulk-upload"])
async def download_template(
    subject_id: UUID,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    """Download the Excel template for bulk question upload."""
    file_bytes = BulkUploadService.generate_template()
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
    current_user: CurrentUser = Depends(TeacherOrAbove),
    file: UploadFile = File(...),
):
    """
    Upload questions in bulk via Excel file.
    Use GET /bulk/template to download the expected format.
    Partial success is supported — valid rows are saved even if some rows fail.
    """
    if not file.filename.endswith((".xlsx", ".xls")):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Only .xlsx and .xls files are accepted.")

    file_bytes = await file.read()
    result = BulkUploadService.process_upload(
        session=session,
        file_bytes=file_bytes,
        filename=file.filename,
        subject_id=subject_id,
        current_user=current_user,
    )
    return result
