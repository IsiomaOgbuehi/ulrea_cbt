import secrets
import string
from uuid import UUID
from sqlmodel import Session, select, func, or_

from fastapi import HTTPException
from sqlmodel import Session, select
from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.api_models.user_api_models import BulkStudentResult, CreateStaffUser, CreateStudent, StudentCreatedResponse, UpdateStudentRequest
from auth.utility.password.password_harsher import PasswordHasher
from auth.services.subscription_service import SubscriptionService
from auth.services.membership_service import MembershipService
from auth.database.schema.membership.membership_db import OrgMembership
from auth.services.user.user_context import UserContext
from auth.database.schema.cohort.cohort_db import CohortMember, TeacherCohortAssignment
from auth.services.teacher_cohort_service import TeacherCohortService


# Who can create whom
CREATION_PERMISSIONS: dict[UserRole, list[UserRole]] = {
    UserRole.SUPER_ADMIN: [UserRole.ADMIN, UserRole.TEACHER, UserRole.SUPERVISOR, UserRole.STUDENT, UserRole.STAFF],
    UserRole.ADMIN: [UserRole.TEACHER, UserRole.SUPERVISOR, UserRole.STAFF, UserRole.STUDENT],
}

class UserManagementService:

    # --------------------------------------------------------
    # ACCESS CODE
    # --------------------------------------------------------

    @classmethod
    def generate_unique_access_code(cls, session: Session) -> str:
        while True:
            code = cls._generate_access_code()
            existing = session.exec(
                select(UserModel).where(UserModel.access_code == code)
            ).first()
            if not existing:
                return code

    @staticmethod
    def _generate_access_code(length: int = 7) -> str:
        """
        Format: STU-8F4K2Q9
        Excludes ambiguous characters: O, 0, I, 1, S, 5
        """
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ2346789"
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        return f"STU-{code}"

    @staticmethod
    def _generate_temp_password(length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$"
        return "".join(secrets.choice(alphabet) for _ in range(length))

    # --------------------------------------------------------
    # STAFF
    # --------------------------------------------------------

    @classmethod
    def create_staff(
        cls,
        session: Session,
        ctx: UserContext,           # ← UserContext instead of creator: UserModel
        payload: CreateStaffUser,
        org_id: UUID,
    ) -> tuple[UserModel, str | None, bool]:
        """
        Returns (user, temp_password, is_existing_user).
        Role and org come from ctx.membership — not from user model.
        If email already exists globally, auto-add to org instead of failing.
        """
        creator_role = ctx.membership.role
        allowed = CREATION_PERMISSIONS.get(creator_role, [])

        if payload.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({creator_role}) cannot create users "
                       f"with role {payload.role}.",
            )

        # Check if user already exists globally
        existing_user = session.exec(
            select(UserModel).where(
                UserModel.email == payload.email.lower().strip()
            )
        ).first()

        if existing_user:
        
            existing_membership = session.exec(
                select(OrgMembership).where(
                    OrgMembership.user_id == existing_user.id,
                    OrgMembership.org_id == org_id,
                    OrgMembership.status.in_([
                        MembershipStatus.ACTIVE, MembershipStatus.PENDING
                    ]),
                )
            ).first()

            if existing_membership:
                raise HTTPException(
                    status_code=409,
                    detail="This user is already a member of your organization.",
                )

            # Auto-add existing user to org
            new_membership = MembershipService.create_pending_membership(
                session=session,
                user_id=existing_user.id,
                org_id=org_id,
                role=payload.role,
                created_by=ctx.user.id,
                institution_id=payload.institution_id,
            )
            session.commit()
            return existing_user, None, True

        # New user — no role or org_id on UserModel
        temp_password = cls._generate_temp_password()

        user = UserModel(
            firstname=payload.firstname,
            lastname=payload.lastname,
            othername=payload.othername or "",
            email=payload.email.lower().strip(),
            phone=payload.phone,
            password=PasswordHasher.create(temp_password),
            verified=False,
            is_first_login=True,
        )
        session.add(user)
        session.flush()  # get user.id

        # Pending membership — activated when staff completes account setup
        MembershipService.create_pending_membership(
            session=session,
            user_id=user.id,
            org_id=org_id,
            role=payload.role,
            created_by=ctx.user.id,
            institution_id=payload.institution_id,
        )

        session.commit()
        session.refresh(user)
        return user, temp_password, False

    # --------------------------------------------------------
    # STUDENT
    # --------------------------------------------------------

    @classmethod
    def create_student(
        cls,
        session: Session,
        ctx: UserContext,           # ← UserContext instead of creator: UserModel
        payload: CreateStudent,
        org_id: UUID,
    ) -> tuple[UserModel, str]:
        """
        Returns (user, access_code).
        No role or org_id stored on UserModel — stored in OrgMembership only.
        """
        creator_role = ctx.membership.role
        allowed = CREATION_PERMISSIONS.get(creator_role, [])

        if UserRole.STUDENT not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({creator_role}) cannot create students.",
            )
        
        if not payload.institution_id:
            raise HTTPException(status_code=400, detail="Please provide institutionId/Reg No/StudentId")

        # Duplicate institution_id within same org
        if payload.institution_id:
            
            existing_inst = session.exec(
                select(OrgMembership).where(
                    OrgMembership.org_id == org_id,
                    OrgMembership.institution_id == payload.institution_id,
                    OrgMembership.role == UserRole.STUDENT,
                )
            ).first()
            if existing_inst:
                raise HTTPException(
                    status_code=409,
                    detail=f"A student with institution ID "
                           f"'{payload.institution_id}' already exists "
                           f"in this organization.",
                )

        # Duplicate email globally
        if payload.email:
            existing_email = session.exec(
                select(UserModel).where(
                    UserModel.email == payload.email.lower().strip()
                )
            ).first()
            if existing_email:
                raise HTTPException(
                    status_code=409,
                    detail=f"A user with email '{payload.email}' "
                           f"already exists.",
                )

        # Access code
        if payload.access_code:
            existing_code = session.exec(
                select(UserModel).where(
                    UserModel.access_code == payload.access_code
                )
            ).first()
            if existing_code:
                raise HTTPException(
                    status_code=409,
                    detail=f"Access code '{payload.access_code}' is already in use.",
                )
            access_code = payload.access_code
        else:
            access_code = cls.generate_unique_access_code(session)

        # No role, no org_id on UserModel
        user = UserModel(
            firstname=payload.firstname,
            lastname=payload.lastname,
            othername=payload.othername or "",
            phone=payload.phone,
            access_code=access_code,
            verified=False,
            is_first_login=True,
            verification_method=VerificationMethod.ACCESS_CODE,
            **({"email": payload.email.lower().strip()} if payload.email else {}),
        )
        session.add(user)
        session.flush()  # get user.id

        # Pending membership — activated when student completes first login setup
        MembershipService.create_pending_membership(
            session=session,
            user_id=user.id,
            org_id=org_id,
            role=UserRole.STUDENT,
            created_by=ctx.user.id,
            institution_id=payload.institution_id,
        )

        session.commit()
        session.refresh(user)
        return user, access_code

    # --------------------------------------------------------
    # BULK STUDENTS
    # --------------------------------------------------------

    @classmethod
    def create_students_bulk(
        cls,
        session: Session,
        ctx: UserContext,           # ← UserContext
        rows: list[dict],
        org_id: UUID,
    ) -> BulkStudentResult:
        successful = []
        errors = []

        for row_num, row in enumerate(rows, start=2):
            try:
                payload = CreateStudent(
                    firstname=row.get("firstname", "").strip(),
                    lastname=row.get("lastname", "").strip(),
                    othername=row.get("othername") or None,
                    email=row.get("email") or None,
                    phone=row.get("phone") or None,
                    institution_id=row.get("institution_id") or None,
                    access_code=row.get("access_code") or None,
                )

                if not payload.firstname or not payload.lastname:
                    errors.append({
                        "row": row_num,
                        "error": "firstname and lastname are required.",
                    })
                    continue

                user, access_code = cls.create_student(
                    session=session,
                    ctx=ctx,
                    payload=payload,
                    org_id=org_id,
                )

                # Read role from membership — not from user
                membership = session.exec(
                    select(OrgMembership).where(
                        OrgMembership.user_id == user.id,
                        OrgMembership.org_id == org_id,
                    )
                ).first()

                successful.append(StudentCreatedResponse(
                    id=user.id,
                    firstname=user.firstname,
                    lastname=user.lastname,
                    phone=user.phone,
                    role=membership.role if membership else UserRole.STUDENT,
                    org_id=org_id,
                    is_first_login=user.is_first_login,
                    access_code=access_code,
                ))

            except HTTPException as e:
                errors.append({"row": row_num, "error": e.detail})
            except Exception as e:
                errors.append({"row": row_num, "error": str(e)})

        return BulkStudentResult(
            total_rows=len(rows),
            successful_rows=len(successful),
            failed_rows=len(errors),
            errors=errors,
            students=successful,
        )
    



    @staticmethod
    def update_student(
        session: Session,
        student_id: UUID,
        payload: UpdateStudentRequest,
        org_id: UUID,
    ) -> UserModel:
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == student_id,
                OrgMembership.org_id == org_id,
                OrgMembership.role == UserRole.STUDENT,
            )
        ).first()
        if not membership:
            raise HTTPException(status_code=404, detail="Student not found in this organization.")

        user = session.get(UserModel, student_id)
        if not user:
            raise HTTPException(status_code=404, detail="Student not found.")

        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(user, key, value)

        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    @staticmethod
    def list_students(
        session: Session,
        org_id: UUID,
        status: MembershipStatus | None = None,
        name: str | None = None,
        cohort_id: UUID | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[tuple[UserModel, OrgMembership]], int]:
        base = (
            select(UserModel, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == UserModel.id)
            .where(
                OrgMembership.org_id == org_id,
                OrgMembership.role == UserRole.STUDENT,
            )
        )
        if status:
            base = base.where(OrgMembership.status == status)
            
        if cohort_id:
            base = base.join(
                CohortMember, CohortMember.student_id == UserModel.id
            ).where(CohortMember.cohort_id == cohort_id)
        
        if name:
            pattern = f"%{name.strip().lower()}%"
            base = base.where(
                or_(
                    func.lower(UserModel.firstname).like(pattern),
                    func.lower(UserModel.lastname).like(pattern),
                )
            )

        total = len(session.exec(base).all())
        rows = session.exec(base.offset((page - 1) * per_page).limit(per_page)).all()
        return rows, total
    


    @staticmethod
    def list_staff(
        session: Session,
        org_id: UUID,
        cohort_id: UUID | None = None,
        name: str | None = None,
        page: int = 1,
        per_page: int = 10,
    ) -> tuple[list[tuple[UserModel, OrgMembership]], int]:
        base = (
            select(UserModel, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == UserModel.id)
            .where(
                OrgMembership.org_id == org_id,
                OrgMembership.role != UserRole.STUDENT,
            )
        )

        if name:
            pattern = f"%{name.strip().lower()}%"
            base = base.where(
                or_(
                    func.lower(UserModel.firstname).like(pattern),
                    func.lower(UserModel.lastname).like(pattern),
                )
            )

        if cohort_id:
            base = base.join(
                TeacherCohortAssignment,
                TeacherCohortAssignment.teacher_id == UserModel.id,
            ).where(TeacherCohortAssignment.cohort_id == cohort_id)

        total = len(session.exec(base).all())
        rows = session.exec(
            base.offset((page - 1) * per_page).limit(per_page)
        ).all()

        return rows, total
    

    @staticmethod
    def to_read_list(session: Session, rows: list[tuple[UserModel, OrgMembership]], org_id: UUID) -> list[dict]:
        results = []
        for user, membership in rows:
            results.append({
                "id": user.id,
                "firstname": user.firstname,
                "lastname": user.lastname,
                "email": user.email,
                "role": membership.role,
                "status": membership.status,
                "cohort_ids": [
                    c.id for c in TeacherCohortService.list_cohorts_for_teacher(session, user.id, org_id)
                ],
                # "subject_ids": TeacherSubjectService.list_subjects_for_teacher(session, user.id, org_id),
            })
        return results