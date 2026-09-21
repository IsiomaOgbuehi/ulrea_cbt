from uuid import UUID
from fastapi import APIRouter, Depends
from cbt_service.database.database import SessionDep
from cbt_service.services.violation.violation_service import ViolationService
from cbt_service.schemas.violation_schemas import ViolationRead
from cbt_service.dependencies import TeacherOrAbove
from cbt_service.schemas.attempt_schemas import CurrentUser

router = APIRouter(prefix="/exams/{exam_id}/violations", tags=["violations"])


@router.get("", response_model=list[ViolationRead])
async def list_exam_violations(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = TeacherOrAbove,
):
    return ViolationService.list_for_exam(session, exam_id, current_user)


@router.patch("/{violation_id}/review", response_model=ViolationRead)
async def review_violation(
    exam_id: UUID,  # kept in path for REST consistency; not otherwise used since violation_id is globally unique
    violation_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = TeacherOrAbove,
):
    return ViolationService.mark_reviewed(session, violation_id, current_user)