from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func, or_

from auth.database.schema.cohort.cohort_db import CohortModel, CohortMember, CohortStatus, TeacherCohortAssignment
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus, UserRole
from auth.database.schema.cohort.cohort_api_models import (
    AddMembersResponse, CohortCreate, CohortUpdate, CohortRead,
    CohortMemberRead, AddMembersRequest, GraduateCohortRequest, PaginatedResponse
)
from auth.services.user.user_context import UserContext


class CohortService:

    # --------------------------------------------------------
    # HELPERS
    # --------------------------------------------------------

    @staticmethod
    def _get_cohort(session: Session, cohort_id: UUID, org_id: UUID) -> CohortModel:
        cohort = session.exec(
            select(CohortModel).where(
                CohortModel.id == cohort_id,
                CohortModel.org_id == org_id,
            )
        ).first()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found.")
        return cohort

    @staticmethod
    def _assert_active(cohort: CohortModel):
        if cohort.status == CohortStatus.GRADUATED:
            raise HTTPException(
                status_code=400,
                detail=f"Cohort '{cohort.name}' has graduated. No changes allowed."
            )
        if cohort.status == CohortStatus.ARCHIVED:
            raise HTTPException(
                status_code=400,
                detail=f"Cohort '{cohort.name}' is archived."
            )

    @staticmethod
    def _member_count(session: Session, cohort_id: UUID) -> int:
        return session.exec(
            select(func.count(CohortMember.id)).where(
                CohortMember.cohort_id == cohort_id
            )
        ).one()

    @staticmethod
    def _to_read(cohort: CohortModel, session: Session) -> CohortRead:
        return CohortRead(
            **cohort.model_dump(),
            member_count=CohortService._member_count(session, cohort.id),
        )

    # --------------------------------------------------------
    # CRUD
    # --------------------------------------------------------

    @staticmethod
    def create(
        session: Session,
        payload: CohortCreate,
        actor: UserModel,
        org_id: UUID,           # ← explicit, from ctx.membership.org_id
    ) -> CohortRead:
        existing = session.exec(
            select(CohortModel).where(
                CohortModel.org_id == org_id,
                CohortModel.name == payload.name,
                CohortModel.status != CohortStatus.ARCHIVED,
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"An active cohort named '{payload.name}' already exists."
            )

        cohort = CohortModel(
            org_id=org_id,
            created_by=actor.id,
            **payload.model_dump(),
        )
        session.add(cohort)
        session.commit()
        session.refresh(cohort)
        return CohortService._to_read(cohort, session)

    @staticmethod
    def get_all(
        session: Session,
        org_id: UUID,
        status: str | None = None,
    ) -> list[CohortRead]:
        # org_id already explicit here — no changes needed
        query = select(CohortModel).where(CohortModel.org_id == org_id)
        if status:
            query = query.where(CohortModel.status == status)
        else:
            query = query.where(CohortModel.status != CohortStatus.ARCHIVED)

        cohorts = session.exec(query).all()
        return [CohortService._to_read(c, session) for c in cohorts]

    @staticmethod
    def get_by_id(
        session: Session,
        cohort_id: UUID,
        ctx: UserContext,
    ) -> CohortRead:

        cohort = CohortService._get_cohort(
            session=session,
            cohort_id=cohort_id,
            org_id=ctx.membership.org_id,
        )

        role = ctx.membership.role

        # Admins can view any cohort in their organization.
        if role in {
            UserRole.SUPER_ADMIN,
            UserRole.ADMIN,
        }:
            return CohortService._to_read(cohort, session)

        # Teachers, supervisors and staff must be assigned
        # to the cohort through TeacherCohortAssignment.
        if role in {
            UserRole.TEACHER,
            UserRole.SUPERVISOR,
            UserRole.STAFF,
        }:
            assignment = session.exec(
                select(TeacherCohortAssignment).where(
                    TeacherCohortAssignment.teacher_id == ctx.user.id,
                    TeacherCohortAssignment.cohort_id == cohort_id,
                    TeacherCohortAssignment.org_id == ctx.membership.org_id,
                )
            ).first()

            if not assignment:
                raise HTTPException(
                    status_code=403,
                    detail="You are not assigned to this cohort.",
                )

            return CohortService._to_read(cohort, session)

        # Students should not reach this point if NonStudent is correct,
        # but keep this as a defensive authorization check.
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to view this cohort.",
        )

    @staticmethod
    def update(
        session: Session,
        cohort_id: UUID,
        payload: CohortUpdate,
        actor: UserModel,
        org_id: UUID,           # ← explicit, was actor.org_id
    ) -> CohortRead:
        cohort = CohortService._get_cohort(session, cohort_id, org_id)
        CohortService._assert_active(cohort)

        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(cohort, key, value)

        cohort.updated_at = datetime.now(timezone.utc)
        session.add(cohort)
        session.commit()
        session.refresh(cohort)
        return CohortService._to_read(cohort, session)

    @staticmethod
    def archive(
        session: Session,
        cohort_id: UUID,
        actor: UserModel,
        org_id: UUID,           # ← explicit, was actor.org_id
    ) -> CohortRead:
        cohort = CohortService._get_cohort(session, cohort_id, org_id)
        CohortService._assert_active(cohort)
        cohort.status = CohortStatus.ARCHIVED
        cohort.updated_at = datetime.now(timezone.utc)
        session.add(cohort)
        session.commit()
        session.refresh(cohort)
        return CohortService._to_read(cohort, session)

    # --------------------------------------------------------
    # GRADUATION
    # --------------------------------------------------------

    @staticmethod
    def graduate(
        session: Session,
        cohort_id: UUID,
        payload: GraduateCohortRequest,
        actor: UserModel,
        org_id: UUID,           # ← explicit, was actor.org_id
    ) -> CohortRead:
        cohort = CohortService._get_cohort(session, cohort_id, org_id)

        if cohort.status == CohortStatus.GRADUATED:
            raise HTTPException(status_code=400, detail="Cohort already graduated.")

        CohortService._assert_active(cohort)

        cohort.status = CohortStatus.GRADUATED
        cohort.graduated_at = datetime.now(timezone.utc)
        cohort.graduated_by = actor.id
        if payload.reason:
            cohort.description = (
                f"{cohort.description or ''} | Graduated: {payload.reason}".strip(" |")
            )
        cohort.updated_at = datetime.now(timezone.utc)
        session.add(cohort)
        session.commit()
        session.refresh(cohort)
        return CohortService._to_read(cohort, session)

    # --------------------------------------------------------
    # MEMBERSHIP
    # --------------------------------------------------------

    @staticmethod
    def add_members(
        session: Session,
        cohort_id: UUID,
        payload: AddMembersRequest,
        actor: UserModel,
        org_id: UUID,           # ← explicit, was actor.org_id
    ) -> AddMembersResponse:
        cohort = CohortService._get_cohort(session, cohort_id, org_id)
        CohortService._assert_active(cohort)

        added = []
        already_in = []
        not_found = []

        for student_id in payload.student_ids:
            # Verify student belongs to this org via OrgMembership — not UserModel
            membership = session.exec(
                select(OrgMembership).where(
                    OrgMembership.user_id == student_id,
                    OrgMembership.org_id == org_id,
                    OrgMembership.role == UserRole.STUDENT,
                    OrgMembership.status == MembershipStatus.ACTIVE,
                )
            ).first()

            if not membership:
                not_found.append(str(student_id))
                continue

            existing = session.exec(
                select(CohortMember).where(
                    CohortMember.cohort_id == cohort_id,
                    CohortMember.student_id == student_id,
                )
            ).first()

            if existing:
                already_in.append(str(student_id))
                continue

            member = CohortMember(
                cohort_id=cohort_id,
                student_id=student_id,
                org_id=org_id,
                added_by=actor.id,
            )
            session.add(member)
            added.append(str(student_id))

        session.commit()

        return AddMembersResponse(
            added=len(added),
            already_members=len(already_in),
            not_found=len(not_found),
            cohort_id=cohort_id
        )

    @staticmethod
    def remove_member(
        session: Session,
        cohort_id: UUID,
        student_id: UUID,
        actor: UserModel,
        org_id: UUID,           # ← explicit, was actor.org_id
    ) -> None:
        cohort = CohortService._get_cohort(session, cohort_id, org_id)
        CohortService._assert_active(cohort)

        member = session.exec(
            select(CohortMember).where(
                CohortMember.cohort_id == cohort_id,
                CohortMember.student_id == student_id,
                CohortMember.org_id == org_id,
            )
        ).first()

        if not member:
            raise HTTPException(status_code=404, detail="Student not in this cohort.")

        session.delete(member)
        session.commit()

    @staticmethod
    def get_members(
        session: Session,
        cohort_id: UUID,
        org_id: UUID,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> PaginatedResponse[CohortMemberRead]:
        CohortService._get_cohort(session, cohort_id, org_id)

        base_query = (
            select(CohortMember, UserModel)
            .join(UserModel, UserModel.id == CohortMember.student_id)
            .where(CohortMember.cohort_id == cohort_id)
        )

        if search:
            term = f"%{search.strip()}%"
            base_query = base_query.where(
                or_(
                    UserModel.firstname.ilike(term),
                    UserModel.lastname.ilike(term),
                    UserModel.email.ilike(term),
                )
            )

        total = session.exec(
            select(func.count()).select_from(base_query.subquery())
        ).one()

        results = session.exec(
            base_query
            .order_by(UserModel.lastname, UserModel.firstname)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        items = [
            CohortMemberRead(
                id=member.id,
                cohort_id=member.cohort_id,
                student_id=member.student_id,
                added_by=member.added_by,
                created_at=member.created_at,
                firstname=user.firstname,
                lastname=user.lastname,
                email=user.email,
            )
            for member, user in results
        ]

        total_pages = (total + page_size - 1) // page_size if total else 0

        return PaginatedResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    # --------------------------------------------------------
    # USED BY EXAM SERVICE
    # --------------------------------------------------------

    @staticmethod
    def get_active_student_ids(
        session: Session,
        cohort_id: UUID,
        org_id: UUID,
    ) -> list[UUID]:
        # org_id already explicit here — no changes needed
        cohort = CohortService._get_cohort(session, cohort_id, org_id)

        if cohort.status == CohortStatus.GRADUATED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot assign exams to graduated cohort '{cohort.name}'."
            )

        members = session.exec(
            select(CohortMember.student_id).where(
                CohortMember.cohort_id == cohort_id,
            )
        ).all()

        return list(members)
    
    @staticmethod
    def count_members(session: Session, cohort_id: UUID) -> int:
        return len(
            session.exec(
                select(CohortMember).where(
                    CohortMember.cohort_id == cohort_id
                )
            ).all()
        )
    

    @staticmethod
    def change_student_cohort(
        session: Session,
        student_id: UUID,
        new_cohort_id: UUID,
        actor: UserModel,
        org_id: UUID,
    ) -> AddMembersResponse:
        # Validate the student actually exists as an active student in this org first
        student_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == student_id,
                OrgMembership.org_id == org_id,
                OrgMembership.role == UserRole.STUDENT,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()
        if not student_membership:
            raise HTTPException(status_code=404, detail="Student not found in this organization.")

        existing = session.exec(
            select(CohortMember)
            .join(CohortModel, CohortModel.id == CohortMember.cohort_id)
            .where(
                CohortMember.student_id == student_id,
                CohortModel.org_id == org_id,
            )
        ).all()

        for member in existing:
            session.delete(member)
        session.commit()

        return CohortService.add_members(
            session=session,
            cohort_id=new_cohort_id,
            payload=AddMembersRequest(student_ids=[student_id]),
            actor=actor,
            org_id=org_id,
        )