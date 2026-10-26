from uuid import UUID
from fastapi import APIRouter, Depends, Response
from cbt_service.schemas.attempt_schemas import CurrentUser
from cbt_service.database.database import SessionDep
from cbt_service.services.violation.violation_service import ViolationService
from cbt_service.schemas.violation_schemas import ViolationCreate, ViolationCreateResult, ViolationRead
from cbt_service.dependencies import StudentOnly
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(prefix="/attempts/{attempt_id}/violations", tags=["violations"])


@router.post("", response_model=ViolationCreateResult, status_code=201)
def report_violation(
    attempt_id: UUID,
    payload: ViolationCreate,
    response: Response,
    session: SessionDep,
    current_user: CurrentUser = StudentOnly,
):
    result: ViolationCreateResult = ViolationService.create(
        session, attempt_id, payload, current_user
    )
    if result.violation is None:
        response.status_code = 200  # attempt already closed: nothing was recorded
    return result


@router.get("", response_model=list[ViolationRead])
def list_attempt_violations(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.VIOLATIONS_VIEW)),
):
    return ViolationService.list_for_attempt(session, attempt_id, current_user)