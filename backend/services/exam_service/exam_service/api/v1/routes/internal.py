from uuid import UUID
from fastapi import APIRouter, Depends
from exam_service.database.database import SessionDep
from exam_service.dependencies import verify_internal_secret
from exam_service.schemas.schemas import ExamSectionInternalRead
from exam_service.services.exam_service import ExamService

router = APIRouter(prefix="/internal/exams", tags=["internal"])


''' GET SECTIONS + ITEM ORDER 📑 '''
@router.get("/{exam_id}/sections-with-items", response_model=list[ExamSectionInternalRead])
async def get_sections_with_items(
    exam_id: UUID,
    session: SessionDep,
    _: None = Depends(verify_internal_secret),
):
    return ExamService.get_sections_with_items_internal(session, exam_id)