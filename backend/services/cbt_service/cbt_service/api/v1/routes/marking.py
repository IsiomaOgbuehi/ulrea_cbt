from uuid import UUID

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from cbt_service.clients.auth_client import AuthClient
from cbt_service.core.permissions import P
from cbt_service.core.redis.redis_client import redis_client
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.dependencies import require_permission
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.item_subject_schemas import PaginatedResponse
from cbt_service.schemas.marking_schemas import (
    MarkingAnswer, MarkingFilter, MarkingOverview, MarkingScriptDetail, MarkingScriptPage, ScriptFilter,
)
from cbt_service.services.marking.marking_service import MarkingService

router = APIRouter(prefix="/exams/{exam_id}/marking", tags=["marking"])


async def _students(student_ids: list[UUID], org_id: UUID) -> dict[UUID, tuple[str | None, str | None]]:
    """Name and student number per student id (from the auth service, cached)."""
    if not student_ids:
        return {}
    users = await AuthClient(redis_client).get_users_bulk(list(dict.fromkeys(student_ids)), org_id=org_id)
    out: dict[UUID, tuple[str | None, str | None]] = {}
    for sid in student_ids:
        user = users.get(str(sid))
        out[sid] = (f"{user.firstname} {user.lastname}".strip(), user.institution_id) if user else (None, None)
    return out


async def _student_names(student_ids: list[UUID], org_id: UUID) -> dict[UUID, str | None]:
    return {sid: name for sid, (name, _) in (await _students(student_ids, org_id)).items()}


''' MARKING OVERVIEW ✍️ '''
@router.get("", response_model=MarkingOverview)
def marking_overview(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_MARK)),
) -> MarkingOverview:
    """Every written question (short answer / essay) on the exam, with answers waiting to be marked."""
    return MarkingService.overview(session, exam_id, current_user)


''' ANSWERS TO ONE QUESTION 📝 '''
@router.get("/{exam_item_id}/answers", response_model=PaginatedResponse[MarkingAnswer])
async def question_answers(
    exam_id: UUID,
    exam_item_id: UUID,
    session: SessionDep,
    status: MarkingFilter = Query(default=MarkingFilter.PENDING),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    anonymous: bool = Query(default=False, description="Leave out student names (blind marking)"),
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_MARK)),
) -> PaginatedResponse[MarkingAnswer]:
    """Students' answers to one question; waiting ones first. Mark with POST /attempts/responses/{id}/review."""
    result: PaginatedResponse[MarkingAnswer] = await run_in_threadpool(
        MarkingService.answers, session, exam_id, exam_item_id, current_user, status, page, page_size,
    )
    await release_connection(session)
    if anonymous or not result.items:
        return result
    names = await _student_names([a.student_id for a in result.items], current_user.org_id)
    for a in result.items:
        a.student_name = names.get(a.student_id)
    return result


''' SCRIPTS (BY STUDENT) 🧑‍🎓 '''
@router.get("/scripts", response_model=MarkingScriptPage)
async def marking_scripts(
    exam_id: UUID,
    session: SessionDep,
    status: ScriptFilter = Query(default=ScriptFilter.ALL),
    search: str | None = Query(default=None, max_length=100, description="Student name or number; or 'Script N'"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    anonymous: bool = Query(default=False, description="Leave out student names (blind marking)"),
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_MARK)),
) -> MarkingScriptPage:
    """Submitted scripts with how many written answers each still has to mark; waiting ones first."""
    scripts = await run_in_threadpool(MarkingService.script_list, session, exam_id, current_user)
    await release_connection(session)
    searching: bool = bool(search and search.strip())
    if searching and not anonymous:
        # Names live in the auth service: look up everyone so the search covers every script.
        people = await _students([s.student_id for s in scripts], current_user.org_id)
        for s in scripts:
            s.student_name, s.student_number = people.get(s.student_id, (None, None))
    result: MarkingScriptPage = MarkingService.page_scripts(scripts, status, search, page, page_size)
    if anonymous:
        for s in result.items:
            s.student_name = s.student_number = None
    elif not searching and result.items:
        people = await _students([s.student_id for s in result.items], current_user.org_id)
        for s in result.items:
            s.student_name, s.student_number = people.get(s.student_id, (None, None))
    return result


''' ONE SCRIPT 📄 '''
@router.get("/scripts/{attempt_id}", response_model=MarkingScriptDetail)
async def marking_script(
    exam_id: UUID,
    attempt_id: UUID,
    session: SessionDep,
    anonymous: bool = Query(default=False, description="Leave out student names (blind marking)"),
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_MARK)),
) -> MarkingScriptDetail:
    """One student's written answers in exam order. Mark with POST /attempts/responses/{id}/review."""
    detail: MarkingScriptDetail = await run_in_threadpool(
        MarkingService.script, session, exam_id, attempt_id, current_user,
    )
    await release_connection(session)
    if anonymous:
        return detail
    name, number = (await _students([detail.student_id], current_user.org_id)).get(detail.student_id, (None, None))
    detail.student_name, detail.student_number = name, number
    for entry in detail.entries:
        if entry.answer is not None:
            entry.answer.student_name = name
    return detail
