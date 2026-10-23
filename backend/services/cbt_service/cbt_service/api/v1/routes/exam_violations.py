from uuid import UUID
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from cbt_service.schemas.item_subject_schemas import UserSummary
from cbt_service.core.redis import redis_client
from cbt_service.clients.auth_client import AuthClient
from cbt_service.database.database import SessionDep, release_connection
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
    violations: list[ViolationRead] = await run_in_threadpool(ViolationService.list_for_exam, session, exam_id, current_user)
    # DB work done: free the connection before the auth-service call.
    await release_connection(session)

    if not violations:
            return violations
    
    auth_client = AuthClient(redis_client)
    users: dict[str, UserSummary] = await auth_client.get_users_bulk(
            [violation.student_id for violation in violations],
            org_id=current_user.org_id,
    )

    enriched: list[ViolationRead] = []
    for violation in violations:
        user: UserSummary | None = users.get(str(violation.student_id))
        name: str | None = f"{user.firstname} {user.lastname}".strip() if user else None
        enriched.append(violation.model_copy(update={"student_name": name}))
    
    return enriched


@router.patch("/{violation_id}/review", response_model=ViolationRead)
def review_violation(
    exam_id: UUID,  # kept in path for REST consistency; not otherwise used since violation_id is globally unique
    violation_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = TeacherOrAbove,
):
    return ViolationService.mark_reviewed(session, violation_id, current_user)