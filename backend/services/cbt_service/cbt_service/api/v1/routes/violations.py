from uuid import UUID
from fastapi import APIRouter, Depends
from cbt_service.schemas.attempt_schemas import CurrentUser
from cbt_service.database.database import SessionDep
from cbt_service.services.violation.violation_service import ViolationService
from cbt_service.schemas.violation_schemas import ViolationCreate, ViolationCreateResult, ViolationRead
from cbt_service.dependencies import StudentOnly, TeacherOrAbove

router = APIRouter(prefix="/attempts/{attempt_id}/violations", tags=["violations"])


@router.post("", response_model=ViolationCreateResult, status_code=201)
async def report_violation(
    attempt_id: UUID,
    payload: ViolationCreate,
    session: SessionDep,
    current_user: CurrentUser = StudentOnly,
):
    return await ViolationService.create(session, attempt_id, payload, current_user)


@router.get("", response_model=list[ViolationRead])
async def list_attempt_violations(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = TeacherOrAbove,
):
    return ViolationService.list_for_attempt(session, attempt_id, current_user)