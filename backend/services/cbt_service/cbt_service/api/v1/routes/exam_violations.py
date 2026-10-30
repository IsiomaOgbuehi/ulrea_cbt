from uuid import UUID
from typing import Any
from fastapi import APIRouter, Depends, Query
from cbt_service.schemas.item_subject_schemas import PaginatedResponse
from cbt_service.services.paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, page_of, total_pages
from fastapi.concurrency import run_in_threadpool
from cbt_service.schemas.item_subject_schemas import UserSummary
from cbt_service.core.redis import redis_client
from cbt_service.clients.auth_client import AuthClient
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.services.violation.violation_service import ViolationService
from cbt_service.schemas.violation_schemas import ViolationRead
from cbt_service.schemas.attempt_schemas import CurrentUser
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

def _paged(items: list[Any], total: int | None, page: int | None, page_size: int) -> Any:
    if page is None or total is None:
        return items
    return PaginatedResponse(items=items, total=total, page=page, page_size=page_size,
                             total_pages=total_pages(total, page_size))


router = APIRouter(prefix="/exams/{exam_id}/violations", tags=["violations"])


@router.get("", response_model=PaginatedResponse[ViolationRead] | list[ViolationRead])
async def list_exam_violations(
    exam_id: UUID,
    session: SessionDep,
    page: int | None = Query(default=None, ge=1, description="Page number (newest first); leave out for the full list"),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    current_user: CurrentUser = Depends(require_permission(P.VIOLATIONS_VIEW)),
) -> PaginatedResponse[ViolationRead] | list[ViolationRead]:
    total: int | None = None
    if page is not None:
        rows, total = await run_in_threadpool(
            lambda: page_of(session, ViolationService.exam_violations_stmt(session, exam_id, current_user), page, page_size)
        )
        violations: list[ViolationRead] = [ViolationRead.model_validate(v, from_attributes=True) for v in rows]
    else:
        violations = await run_in_threadpool(ViolationService.list_for_exam, session, exam_id, current_user)
    # DB work done: free the connection before the auth-service call.
    await release_connection(session)

    if not violations:
        return _paged(violations, total, page, page_size)
    
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

    return _paged(enriched, total, page, page_size)


@router.patch("/{violation_id}/review", response_model=ViolationRead)
def review_violation(
    exam_id: UUID,  # kept in path for REST consistency; not otherwise used since violation_id is globally unique
    violation_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.VIOLATIONS_REVIEW)),
):
    return ViolationService.mark_reviewed(session, violation_id, current_user)