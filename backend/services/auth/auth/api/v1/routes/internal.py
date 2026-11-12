from datetime import datetime
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from uuid import UUID
from sqlmodel import col, select
from auth.database.schema.user.user_db import UserModel
from auth.database.database import SessionDep
from auth.core.settings import settings
from auth.services.cohort_service import CohortService
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus, UserRole

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
    institution_id: str | None = None   # the org's own number for the person (e.g. student number)


class BulkUserRequest(BaseModel):
    user_ids: list[UUID]
    org_id: UUID


class FilterStudentsRequest(BaseModel):
    user_ids: list[UUID] = Field(max_length=500)
    org_id: UUID


@router.get(
    "/users/{user_id}",
    response_model=UserSummaryResponse,
)
def get_user_internal(
    user_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    result = session.exec(
        select(UserModel, OrgMembership)
        .outerjoin(
            OrgMembership,
            OrgMembership.user_id == UserModel.id,
        )
        .where(UserModel.id == user_id)
    ).first()

    if not result:
        raise HTTPException(status_code=404, detail="User not found.")

    user, membership = result

    return UserSummaryResponse(
        id=user.id,
        firstname=user.firstname,
        lastname=user.lastname,
        email=user.email,
        role=membership.role if membership else None,
    )


@router.post(
    "/users/bulk",
    response_model=list[UserSummaryResponse],
)
def get_users_bulk_internal(
    payload: BulkUserRequest,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    if not payload.user_ids:
        return []

    results = session.exec(
        select(UserModel, OrgMembership)
        .outerjoin(
            OrgMembership,
            (OrgMembership.user_id == UserModel.id)
            & (OrgMembership.org_id == payload.org_id),
        )
        .where(UserModel.id.in_(payload.user_ids))
    ).all()

    return [
        UserSummaryResponse(
            id=user.id,
            firstname=user.firstname,
            lastname=user.lastname,
            email=user.email,
            role=membership.role if membership else None,
            institution_id=membership.institution_id if membership else None,
        )
        for user, membership in results
    ]



@router.get("/cohorts/{cohort_id}/student_ids")
def get_cohort_student_ids_internal(
    cohort_id: UUID,
    session: SessionDep,
    x_org_id: UUID = Header(...),
    _: str = Depends(verify_internal_secret),
):
    """Called by exam service to get student IDs for cohort assignment."""
    student_ids = CohortService.get_active_student_ids(session, cohort_id, x_org_id)
    return [str(i) for i in student_ids]



@router.post("/students/filter", response_model=list[UUID])
def filter_active_students_internal(
    payload: FilterStudentsRequest,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
) -> list[UUID]:
    """Return the subset of user_ids that are ACTIVE students in org_id."""
    if not payload.user_ids:
        return []

    valid_ids: list[UUID] = list(
        session.exec(
            select(OrgMembership.user_id).where(
                OrgMembership.org_id == payload.org_id,
                col(OrgMembership.user_id).in_(payload.user_ids),
                OrgMembership.role == UserRole.STUDENT,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).all()
    )
    return list(dict.fromkeys(valid_ids))

class ExamStartsRead(BaseModel):
    allowed: bool
    reason: str | None = None


@router.get("/orgs/{org_id}/exam-starts", response_model=ExamStartsRead)
def org_exam_starts(
    org_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
) -> ExamStartsRead:
    """Whether the organisation's candidates may start new exams: not once its
    trial or paid period has ended (and only while limits are enforced).
    Exams already under way are never affected. Public organisations (exam
    bodies) are governed by their candidates' own subscriptions instead."""
    from auth.database.schema.organization.enums import OrganizationVisibility
    from auth.database.schema.organization.organization_db import OrganizationModel
    from auth.services.billing import org_billing

    org = session.get(OrganizationModel, org_id)
    if org is None or org.visibility == OrganizationVisibility.PUBLIC:
        return ExamStartsRead(allowed=True)
    st = org_billing.status(session, org)
    if not st.enforced or st.state in ("trial", "active"):
        return ExamStartsRead(allowed=True)
    return ExamStartsRead(
        allowed=False,
        reason="Your organisation's plan has ended, so new exams can't be started. Please let your school or organisation know.",
    )


class ExamAccessRead(BaseModel):
    public: bool
    allowed: bool
    expires_at: datetime | None = None


@router.get("/exam-access", response_model=ExamAccessRead)
def exam_access_internal(
    student_id: UUID,
    exam_body_org_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
) -> ExamAccessRead:
    """For the exam service: is this an exam body, and may the student practise its exams."""
    from auth.services.billing import exam_access
    a = exam_access.check(session, student_id, exam_body_org_id)
    return ExamAccessRead(public=a.public, allowed=a.active, expires_at=a.expires_at)


class ExamBodyRef(BaseModel):
    id: UUID
    name: str


@router.get("/exam-bodies", response_model=list[ExamBodyRef])
def exam_bodies_internal(session: SessionDep, _: str = Depends(verify_internal_secret)) -> list[ExamBodyRef]:
    """For the exam service: exam bodies sharing questions now (public organisations)."""
    from auth.database.schema.organization.enums import OrganizationVisibility
    from auth.database.schema.organization.organization_db import OrganizationModel

    return [
        ExamBodyRef(id=o.id, name=o.name) for o in session.exec(
            select(OrganizationModel).where(OrganizationModel.visibility == OrganizationVisibility.PUBLIC)
            .order_by(col(OrganizationModel.name))
        ).all()
    ]


@router.get("/orgs/{org_id}/question-library", response_model=ExamStartsRead)
def org_question_library(
    org_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
) -> ExamStartsRead:
    """Whether the organisation may copy exam bodies' questions: free on a trial
    or a paid plan (whether or not limits are enforced). Exam bodies may too."""
    from auth.database.schema.organization.enums import OrganizationVisibility
    from auth.database.schema.organization.organization_db import OrganizationModel
    from auth.services.billing import org_billing

    org = session.get(OrganizationModel, org_id)
    if org is None:
        return ExamStartsRead(allowed=False, reason="Organisation not found.")
    if org.visibility == OrganizationVisibility.PUBLIC or org_billing.status(session, org).state in ("trial", "active"):
        return ExamStartsRead(allowed=True)
    return ExamStartsRead(
        allowed=False,
        reason="Questions from exam bodies come free with a plan. Your plan has ended: renew it under Billing to use them.",
    )
