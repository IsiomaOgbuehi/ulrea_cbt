from uuid import UUID
from typing import Literal
from auth.database.schema.cohort.enum import MemberStatus
from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.database.schema.cohort.cohort_api_models import (
    AddMembersResponse, AssignTeacherRequest, CohortCreate, CohortUpdate, CohortRead,
    AddMembersRequest, CohortDeletableRead, CohortMemberRead, GraduateCohortRequest, GraduateCohortResult, MemberActionRequest, MemberActionResult, MyCohortRead,
    PaginatedResponse, TeacherCohortAssignmentRead
)
from auth.services.cohort_service import CohortService
from auth.clients.cbt_service_client import cbt_service_client
from fastapi import HTTPException
from auth.utility.redis.redis_client import redis_client
from auth.dependencies.auth_dependencies import get_user_context, require_cohort_access
from auth.services.user.user_context import UserContext
from auth.services.teacher_cohort_service import TeacherCohortService
from auth.database.schema.cohort.enum import CohortStatus
from auth.core.permissions import P
from auth.dependencies.auth_dependencies import require_candidate_or, require_permission

router = APIRouter(prefix="/cohorts", tags=["cohorts"])




''' CREATE COHORT 📚 '''
@router.post("", response_model=CohortRead)
def create_cohort(
    payload: CohortCreate,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    return CohortService.create(session=session, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)


# ''' LIST COHORTS 📋 '''
# @router.get("", response_model=list[CohortRead])
# def list_cohorts(
#     session: SessionDep,
#     ctx: UserContext = Depends(TeacherOrAbove),
#     status: str | None = Query(default=None, description="Filter by status: active, graduated, archived"),
# ):
#     return CohortService.get_all(session, ctx.membership.org_id, status)


''' LIST COHORTS 📋 '''
@router.get("", response_model=PaginatedResponse[CohortRead])
def list_cohorts(
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_VIEW)),
    status: CohortStatus | None = Query(default=None, description="active, graduated or archived; default excludes archived"),
    search: str | None = Query(default=None, max_length=100, description="Matches cohort name"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PaginatedResponse[CohortRead]:
    cohorts, total = CohortService.get_all(
        session, ctx.membership.org_id,
        status=status, search=search, page=page, page_size=page_size,
        # Without org-wide reach, only the groups this person is assigned to.
        assigned_to=None if ctx.reach(P.COHORTS_VIEW) == "all" else ctx.user.id,
    )
    return PaginatedResponse(
        items=cohorts,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


''' MY COHORTS (TEACHER) 🧑‍🏫 '''
@router.get("/my-cohorts", response_model=list[MyCohortRead])
def list_my_cohorts(
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_VIEW)),
):
    """
    Cohorts the current teacher is assigned to.
    Used to populate the cohort picker when creating an exam.
    """
    cohorts = TeacherCohortService.list_cohorts_for_teacher(
        session=session, teacher_id=ctx.user.id, org_id=ctx.membership.org_id
    )
    return [
        MyCohortRead(
            id=c.id,
            name=c.name,
            status=c.status,
            student_count=CohortService.count_members(session, c.id),
        )
        for c in cohorts
    ]


''' GET COHORT 🔍 '''
@router.get("/{cohort_id}", response_model=CohortRead)
def get_cohort(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_VIEW)),
):
    return CohortService.get_by_id(session=session, cohort_id=cohort_id, ctx=ctx)


''' UPDATE COHORT ✏️ '''
@router.patch("/{cohort_id}", response_model=CohortRead)
def update_cohort(
    cohort_id: UUID,
    payload: CohortUpdate,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    return CohortService.update(session=session, cohort_id=cohort_id, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)


''' ARCHIVE COHORT 🗄️ '''
@router.delete("/{cohort_id}", response_model=CohortRead)
def archive_cohort(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    return CohortService.archive(session=session, cohort_id=cohort_id, actor=ctx.user, org_id=ctx.membership.org_id)


''' GRADUATE COHORT 🎓 '''
@router.post("/{cohort_id}/graduate", response_model=GraduateCohortResult)
async def graduate_cohort(
    cohort_id: UUID,
    payload: GraduateCohortRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> GraduateCohortResult:
    """
    Graduate every current student in the cohort. Their accounts stay active
    and their records are kept. Then (`then`):
    - keep_open (default): the cohort stays open for the next set of students
    - archive: the cohort is archived (for a group you won't use again)
    """
    result: GraduateCohortResult = await run_in_threadpool(
        CohortService.graduate, session=session, cohort_id=cohort_id, payload=payload,
        actor=ctx.user, org_id=ctx.membership.org_id,
    )
    try:
        await redis_client.delete(f"cohort_students:{cohort_id}")
    except Exception:
        pass
    return result


''' ADD MEMBERS TO COHORT 👥 '''
@router.post("/{cohort_id}/members", response_model=AddMembersResponse)
async def add_members(
    cohort_id: UUID,
    payload: AddMembersRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> AddMembersResponse:
    result: AddMembersResponse = await run_in_threadpool(
        CohortService.add_members,
        session=session,
        cohort_id=cohort_id,
        payload=payload,
        actor=ctx.user,
        org_id=ctx.membership.org_id,
    )

    # Invalidate exam service cache after membership change
    try:
        await redis_client.delete(f"cohort_students:{cohort_id}")
    except Exception:
        pass

    return result


''' REMOVE MEMBER FROM COHORT ❌ '''
@router.delete("/{cohort_id}/members/{student_id}", status_code=204)
def remove_member(
    cohort_id: UUID,
    student_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    CohortService.remove_member(session=session, cohort_id=cohort_id, student_id=student_id, actor=ctx.user, org_id=ctx.membership.org_id)


''' VIEW COHORT MEMBERS 👀 '''
@router.get("/{cohort_id}/members", response_model=PaginatedResponse[CohortMemberRead])
def get_members(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_cohort_access),
    search: str | None = Query(None, description="Search by firstname, lastname, or email"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: MemberStatus | Literal["all"] = Query(MemberStatus.ACTIVE, description="active (default), graduated, left or all"),
):
    """
    Admins can view members of any cohort in their org.
    Teachers/staff can only view members of cohorts they're assigned to.
    """
    return CohortService.get_members(
        session=session,
        cohort_id=cohort_id,
        org_id=ctx.membership.org_id,
        search=search,
        page=page,
        page_size=page_size,
        status=None if status == "all" else status,
    )


''' ALL MEMBER IDS (FOR "SELECT ALL") ☑️ '''
@router.get("/{cohort_id}/members/ids", response_model=list[UUID])
def get_member_ids(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
    search: str | None = Query(None),
    status: MemberStatus = Query(MemberStatus.ACTIVE),
) -> list[UUID]:
    """Every matching member's student id, so "select all" can cover every page."""
    return CohortService.member_ids(session, cohort_id, ctx.membership.org_id, status, search)


''' GRADUATE / MOVE / REMOVE CHOSEN STUDENTS 🎓 '''
@router.post("/{cohort_id}/members/actions", response_model=MemberActionResult)
async def member_action(
    cohort_id: UUID,
    payload: MemberActionRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> MemberActionResult:
    """
    Graduate, move (to another cohort) or remove the chosen students. The
    cohort stays open for new students; their records are kept as history.
    Graduated students' accounts stay active (they can still sign in).
    """
    result: MemberActionResult = await run_in_threadpool(
        CohortService.member_action,
        session=session, cohort_id=cohort_id, payload=payload,
        actor=ctx.user, org_id=ctx.membership.org_id,
    )
    for cid in {cohort_id, *( [result.target_cohort_id] if result.target_cohort_id else [] )}:
        try:
            await redis_client.delete(f"cohort_students:{cid}")
        except Exception:
            pass
    return result


''' REOPEN A CLOSED COHORT 🔓 '''
@router.post("/{cohort_id}/reopen", response_model=CohortRead)
def reopen_cohort(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> CohortRead:
    """Reopen an archived (or older-style closed) cohort so it can take students again."""
    return CohortService.reopen(session, cohort_id, ctx.membership.org_id)


''' ASSIGN TEACHER TO COHORT 🧑‍🏫➕ '''
@router.post("/{cohort_id}/teachers", response_model=TeacherCohortAssignmentRead)
def assign_teacher(
    cohort_id: UUID,
    payload: AssignTeacherRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    assignment = TeacherCohortService.assign(
        session=session,
        cohort_id=cohort_id,
        teacher_id=payload.teacher_id,
        org_id=ctx.membership.org_id,
        actor_id=ctx.user.id,
    )
    return TeacherCohortAssignmentRead.model_validate(assignment, from_attributes=True)


''' UNASSIGN TEACHER FROM COHORT 🧑‍🏫➖ '''
@router.delete("/{cohort_id}/teachers/{teacher_id}", status_code=204)
def unassign_teacher(
    cohort_id: UUID,
    teacher_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
):
    TeacherCohortService.unassign(
        session=session, cohort_id=cohort_id, teacher_id=teacher_id, org_id=ctx.membership.org_id
    )


''' LIST TEACHERS ASSIGNED TO COHORT 📋 '''
@router.get("/{cohort_id}/teachers", response_model=list[TeacherCohortAssignmentRead])
def list_cohort_teachers(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_cohort_access),
):
    """
    Admins see this for any cohort; assigned teachers can see their
    co-teachers on cohorts they're part of.
    """
    rows = TeacherCohortService.list_teachers_for_cohort(
        session=session, cohort_id=cohort_id, org_id=ctx.membership.org_id
    )
    return [
        TeacherCohortAssignmentRead(
            id=assignment.id,
            teacher_id=assignment.teacher_id,
            teacher_name=f"{teacher.firstname} {teacher.lastname}".strip(),
            cohort_id=assignment.cohort_id,
            assigned_by=assignment.assigned_by,
            assigned_at=assignment.assigned_at,
        )
        for assignment, teacher in rows
    ]


async def _deletion_blocker(session: SessionDep, cohort_id: UUID, org_id: UUID) -> str | None:
    blocker: str | None = await run_in_threadpool(CohortService.local_deletion_blocker, session, cohort_id, org_id)
    if blocker:
        return blocker
    if await cbt_service_client.get_cohort_exam_assignment_count(cohort_id, org_id) > 0:
        return "It has been given exams. Archive it instead to keep the results grouped under it."
    return None


''' CAN THIS COHORT BE DELETED? 🗑️ '''
@router.get("/{cohort_id}/deletable", response_model=CohortDeletableRead)
async def cohort_deletable(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> CohortDeletableRead:
    """Only a cohort that never had students or exams (e.g. made by mistake) can be deleted."""
    try:
        blocker: str | None = await _deletion_blocker(session, cohort_id, ctx.membership.org_id)
    except HTTPException as e:
        if e.status_code == 404:
            raise
        return CohortDeletableRead(deletable=False, reason="Couldn't check its exams right now.")
    return CohortDeletableRead(deletable=blocker is None, reason=blocker)


''' DELETE COHORT PERMANENTLY 🗑️ '''
@router.delete("/{cohort_id}/permanently", status_code=204)
async def delete_cohort_permanently(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.COHORTS_MANAGE)),
) -> None:
    """
    Delete a cohort for good. Refused (409) if it ever had students or exams:
    archive those instead so their history stays intact.
    """
    blocker: str | None = await _deletion_blocker(session, cohort_id, ctx.membership.org_id)
    if blocker:
        raise HTTPException(status_code=409, detail=f"This cohort can't be deleted. {blocker}")
    await run_in_threadpool(CohortService.delete_permanently, session, cohort_id, ctx.membership.org_id)
