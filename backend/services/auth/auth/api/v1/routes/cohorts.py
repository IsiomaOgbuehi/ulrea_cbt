from uuid import UUID
from fastapi import APIRouter, Depends, Query
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.database.schema.cohort.cohort_api_models import (
    AddMembersResponse, AssignTeacherRequest, CohortCreate, CohortUpdate, CohortRead,
    AddMembersRequest, CohortMemberRead, GraduateCohortRequest, MyCohortRead, TeacherCohortAssignmentRead
)
from auth.services.cohort_service import CohortService
from auth.utility.redis.redis_client import redis_client
from auth.dependencies.auth_dependencies import get_user_context, require_cohort_access
from auth.services.user.user_context import UserContext
from auth.services.teacher_cohort_service import TeacherCohortService
from .users import require_roles

router = APIRouter(prefix="/cohorts", tags=["cohorts"])


AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER, UserRole.STAFF)


''' CREATE COHORT 📚 '''
@router.post("", response_model=CohortRead)
async def create_cohort(
    payload: CohortCreate,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.create(session=session, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)


''' LIST COHORTS 📋 '''
@router.get("", response_model=list[CohortRead])
async def list_cohorts(
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
    status: str | None = Query(default=None, description="Filter by status: active, graduated, archived"),
):
    return CohortService.get_all(session, ctx.membership.org_id, status)


''' MY COHORTS (TEACHER) 🧑‍🏫 '''
@router.get("/my-cohorts", response_model=list[MyCohortRead])
async def list_my_cohorts(
    session: SessionDep,
    ctx: UserContext = Depends(TeacherOrAbove),
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
async def get_cohort(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.get_by_id(session=session, cohort_id=cohort_id, org_id=ctx.membership.org_id)


''' UPDATE COHORT ✏️ '''
@router.patch("/{cohort_id}", response_model=CohortRead)
async def update_cohort(
    cohort_id: UUID,
    payload: CohortUpdate,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.update(session=session, cohort_id=cohort_id, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)


''' ARCHIVE COHORT 🗄️ '''
@router.delete("/{cohort_id}", response_model=CohortRead)
async def archive_cohort(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.archive(session=session, cohort_id=cohort_id, actor=ctx.user, org_id=ctx.membership.org_id)


''' GRADUATE COHORT 🎓 '''
@router.post("/{cohort_id}/graduate", response_model=CohortRead)
async def graduate_cohort(
    cohort_id: UUID,
    payload: GraduateCohortRequest,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    """
    Mark a cohort as graduated.
    After graduation:
    - No new students can be added
    - No new exams can be assigned
    - All historical data is preserved
    """
    return CohortService.graduate(session=session, cohort_id=cohort_id, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)


''' ADD MEMBERS TO COHORT 👥 '''
@router.post("/{cohort_id}/members", response_model=AddMembersResponse)
async def add_members(
    cohort_id: UUID,
    payload: AddMembersRequest,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    result = CohortService.add_members(session=session, cohort_id=cohort_id, payload=payload, actor=ctx.user, org_id=ctx.membership.org_id)

    # Invalidate exam service cache after membership change
    try:
        await redis_client.delete(f"cohort_students:{cohort_id}")
    except Exception:
        pass

    return result


''' REMOVE MEMBER FROM COHORT ❌ '''
@router.delete("/{cohort_id}/members/{student_id}", status_code=204)
async def remove_member(
    cohort_id: UUID,
    student_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    CohortService.remove_member(session=session, cohort_id=cohort_id, student_id=student_id, actor=ctx.user, org_id=ctx.membership.org_id)


''' VIEW COHORT MEMBERS 👀 '''
@router.get("/{cohort_id}/members", response_model=list[CohortMemberRead])
async def get_members(
    cohort_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(require_cohort_access),
):
    """
    Admins can view members of any cohort in their org.
    Teachers/staff can only view members of cohorts they're assigned to.
    """
    return CohortService.get_members(session=session, cohort_id=cohort_id, org_id=ctx.membership.org_id)


''' ASSIGN TEACHER TO COHORT 🧑‍🏫➕ '''
@router.post("/{cohort_id}/teachers", response_model=TeacherCohortAssignmentRead)
async def assign_teacher(
    cohort_id: UUID,
    payload: AssignTeacherRequest,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
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
async def unassign_teacher(
    cohort_id: UUID,
    teacher_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(AdminOrAbove),
):
    TeacherCohortService.unassign(
        session=session, cohort_id=cohort_id, teacher_id=teacher_id, org_id=ctx.membership.org_id
    )


''' LIST TEACHERS ASSIGNED TO COHORT 📋 '''
@router.get("/{cohort_id}/teachers", response_model=list[TeacherCohortAssignmentRead])
async def list_cohort_teachers(
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