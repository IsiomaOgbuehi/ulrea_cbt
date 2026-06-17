from uuid import UUID
from fastapi import APIRouter, Depends, Query
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.database.schema.cohort.cohort_api_models import (
    CohortCreate, CohortUpdate, CohortRead,
    AddMembersRequest, CohortMemberRead, GraduateCohortRequest
)
from auth.services.cohort_service import CohortService
from auth.utility.redis.redis_client import redis_client
from auth.dependencies.auth_dependencies import get_user_context
from auth.services.user.user_context import UserContext
from .users import require_roles

router = APIRouter(prefix="/cohorts", tags=["cohorts"])


AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER, UserRole.STAFF)


''' CREATE COHORT 📚 '''
@router.post("", response_model=CohortRead)
async def create_cohort(
    payload: CohortCreate,
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
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
@router.post("/{cohort_id}/members", response_model=dict)
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
    ctx: UserContext = Depends(AdminOrAbove),
):
    return CohortService.get_members(session=session, cohort_id=cohort_id, org_id=ctx.membership.org_id)