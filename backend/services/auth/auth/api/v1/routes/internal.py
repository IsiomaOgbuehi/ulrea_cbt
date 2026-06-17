from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from uuid import UUID
from sqlmodel import select
from auth.database.schema.user.user_db import UserModel
from auth.database.database import SessionDep
from auth.core.settings import settings
from auth.services.cohort_service import CohortService
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import UserRole

router = APIRouter(prefix="/internal", tags=["internal"])

def verify_internal_secret(x_internal_secret: str = Header(...)):
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden.")
    
class UserSummaryResponse(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    email: str | None
    role: UserRole | None


class BulkUserRequest(BaseModel):
    user_ids: list[UUID]


@router.get("/users/{user_id}", response_model=UserSummaryResponse)
async def get_user_internal(
    user_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    # user = session.exec(
    #     select(UserModel).where(UserModel.id == user_id)
    # ).first()
    user, membership = session.exec(
        select(UserModel, OrgMembership)
        .join(
            OrgMembership,
            OrgMembership.user_id == UserModel.id,
            isouter=True,
        )
        .where(UserModel.id == user_id)
    ).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    return UserSummaryResponse(
        id=user.id,
        firstname=user.firstname,
        lastname=user.lastname,
        email=user.email,
        role=membership.role if membership else None,
    )


@router.post("/users/bulk", response_model=list[UserSummaryResponse])
async def get_users_bulk_internal(
    payload: BulkUserRequest,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    # users = session.exec(
    #     select(UserModel).where(UserModel.id.in_(payload.user_ids))
    # ).all()

    results = session.exec(
        select(UserModel, OrgMembership)
        .join(
            OrgMembership,
            OrgMembership.user_id == UserModel.id,
            isouter=True,
        )
        .where(UserModel.id.in_(payload.user_ids))
    ).all()

    return [
        UserSummaryResponse(
            id=u.id,
            firstname=u.firstname,
            lastname=u.lastname,
            email=u.email,
            role=m.role.value if m else None,
        )
        for u, m in results
    ]
    # return [UserSummaryResponse.model_validate(u, from_attributes=True) for u in users]




@router.get("/cohorts/{cohort_id}/student_ids")
async def get_cohort_student_ids_internal(
    cohort_id: UUID,
    session: SessionDep,
    x_org_id: UUID = Header(...),
    _: str = Depends(verify_internal_secret),
):
    """Called by exam service to get student IDs for cohort assignment."""
    student_ids = CohortService.get_active_student_ids(session, cohort_id, x_org_id)
    return [str(i) for i in student_ids]