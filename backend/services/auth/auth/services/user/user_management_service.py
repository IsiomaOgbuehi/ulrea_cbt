import secrets
import string
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select
from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.api_models.user_api_models import BulkStudentResult, CreateStaffUser, CreateStudent, StudentCreatedResponse
from auth.utility.password.password_harsher import PasswordHasher
from auth.services.subscription_service import SubscriptionService
from auth.services.membership_service import MembershipService
from auth.database.schema.membership.membership_db import OrgMembership
from auth.services.user.user_context import UserContext


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

# class UserManagementService:

#     @classmethod
#     def generate_unique_access_code(
#         cls,
#         session: SessionDep,
#     ) -> str:
#         while True:
#             code = cls._generate_access_code()

#             existing = session.exec(
#                 select(UserModel).where(
#                     UserModel.access_code == code
#                 )
#             ).first()

#             if not existing:
#                 return code

#     @staticmethod
#     def _generate_access_code(length: int = 7) -> str:
#         """
#         Generates student access codes like:
#         STU-8F4K2Q9

#         Excludes ambiguous characters:
#         O, 0, I, 1, S, 5
#         """

#         alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ2346789"

#         code = "".join(
#             secrets.choice(alphabet)
#             for _ in range(length)
#         )

#         return f"STU-{code}"
    
#     staticmethod
#     def _generate_temp_password(length: int = 12) -> str:
#         alphabet = string.ascii_letters + string.digits + "!@#$"
#         return "".join(secrets.choice(alphabet) for _ in range(length))
    
#     @classmethod
#     def create_staff(
#         cls,
#         session: SessionDep,
#         creator: UserModel,
#         payload: CreateStaffUser,
#         org_id: UUID,
#     ) -> tuple[UserModel, str | None, bool]:
#         """
#         Returns (user, temp_password, is_existing_user).
#         If email already exists globally — auto-add to org, no new password.
#         """

#         creator_membership = MembershipService.get_active_membership(
#             session=session,
#             user_id=creator.id,
#         )

#         allowed = CREATION_PERMISSIONS.get(
#             creator_membership.role,
#             [],
#         )

#         if creator_membership.role not in allowed:
#             raise HTTPException(
#                 status_code=403,
#                 detail=f"{creator.role} cannot create users with role {payload.role}."
#             )

#         existing = session.exec(
#             select(UserModel).where(
#                 UserModel.email == payload.email.lower().strip()
#             )
#         ).first()

#         if existing:
#             # Auto-add to this org instead of failing
#             membership, user, _ = SubscriptionService.add_existing_user_to_org(
#                 session=session,
#                 email=payload.email,
#                 role=payload.role,
#                 org_id=org_id,
#                 creator=creator,
#                 institution_id=payload.institution_id,
#             )
#             return user, None, True     # no temp password for existing user

#         temp_password = cls._generate_temp_password()

#         user_data = {
#             "firstname": payload.firstname,
#             "lastname": payload.lastname,
#             "othername": payload.othername or "",
#             "email": payload.email.lower().strip(),
#             "phone": payload.phone,
#             # "role": payload.role,
#             # "org_id": org_id,
#             "password": PasswordHasher.create(temp_password),
#             "verified": False,
#             "is_first_login": True,
#         }

#         # Optional institution ID
#         # if payload.institution_id:
#         #     user_data["institution_id"] = payload.institution_id

#         user = UserModel(**user_data)

#         session.add(user)

#         # After creating user, create pending membership immediately
#         MembershipService.create_pending_membership(
#             session=session,
#             user_id=user.id,
#             org_id=org_id,
#             role=payload.role,
#             created_by=creator.id,
#             institution_id=payload.institution_id,
#         )

#         session.commit()
#         session.refresh(user)

#         return user, temp_password, False
    

#     @classmethod
#     def create_student(
#         cls,
#         session: SessionDep,
#         creator: UserModel,
#         payload: CreateStudent,
#         org_id: UUID,
#     ) -> tuple[UserModel, str]:
#         """Returns (user, access_code)."""

#         allowed = CREATION_PERMISSIONS.get(creator.role, [])
#         if UserRole.STUDENT not in allowed:
#             raise HTTPException(
#                 status_code=403,
#                 detail=f"{creator.role} cannot create students."
#             )
        
#         # Check duplicate institution_id within same org
#         if payload.institution_id:
#             existing_institution = session.exec(
#                 select(UserModel).where(
#                     UserModel.institution_id == payload.institution_id,
#                     UserModel.org_id == org_id,
#                     UserModel.role == UserRole.STUDENT,
#                 )
#             ).first()
#             if existing_institution:
#                 raise HTTPException(
#                     status_code=409,
#                     detail=f"A student with institution ID '{payload.institution_id}' already exists in this organization."
#                 )
            
#         # Check duplicate email within same org if provided
#         if payload.email:
#             existing_email = session.exec(
#                 select(UserModel).where(
#                     UserModel.email == payload.email.lower().strip(),
#                     UserModel.org_id == org_id,
#                 )
#             ).first()
#             if existing_email:
#                 raise HTTPException(
#                     status_code=409,
#                     detail=f"A user with email '{payload.email}' already exists in this organization."
#                 )

#         # Ensure access code is unique
#         # while True:
#         #     access_code = cls.generate_unique_access_code(session)

#         #     existing = session.exec(
#         #         select(UserModel).where(
#         #             UserModel.access_code == access_code
#         #         )
#         #     ).first()

#         #     if not existing:
#         #         break


#         # Use provided access code or generate one
#         if payload.access_code:
#             # Validate it's unique globally
#             existing_code = session.exec(
#                 select(UserModel).where(UserModel.access_code == payload.access_code)
#             ).first()
#             if existing_code:
#                 raise HTTPException(
#                     status_code=409,
#                     detail=f"Access code '{payload.access_code}' is already in use."
#                 )
#             access_code = payload.access_code
#         else:
#             access_code = cls.generate_unique_access_code(session)

#         user_data = {
#             "firstname": payload.firstname,
#             "lastname": payload.lastname,
#             "othername": payload.othername or "",
#             "phone": payload.phone,
#             "role": UserRole.STUDENT,
#             "org_id": org_id,
#             "access_code": access_code,
#             "verified": False,
#             "is_first_login": True,
#         }

#         # Only add email if provided
#         if payload.email:
#             user_data["email"] = payload.email.lower().strip()

#         # Only add institution_id if provided
#         if payload.institution_id:
#             user_data["institution_id"] = payload.institution_id

#         user = UserModel(**user_data)

#         session.add(user)
#         session.commit()
#         session.refresh(user)

#         return user, access_code
    

#     @classmethod
#     def create_students_bulk(
#         cls,
#         session: SessionDep,
#         creator: UserModel,
#         rows: list[dict],
#         org_id: UUID,
#     ) -> BulkStudentResult:
#         """Process multiple students from parsed Excel rows."""

#         successful = []
#         errors = []

#         for row_num, row in enumerate(rows, start=2):  # start=2 accounts for header row
#             try:
#                 payload = CreateStudent(
#                     firstname=row.get("firstname", "").strip(),
#                     lastname=row.get("lastname", "").strip(),
#                     othername=row.get("othername") or None,
#                     email=row.get("email") or None,
#                     phone=row.get("phone") or None,
#                     institution_id=row.get("institution_id") or None,
#                     access_code=row.get("access_code") or None,
#                 )

#                 if not payload.firstname or not payload.lastname:
#                     errors.append({"row": row_num, "error": "firstname and lastname are required."})
#                     continue

#                 user, access_code = cls.create_student(
#                     session=session,
#                     creator=creator,
#                     payload=payload,
#                     org_id=org_id,
#                 )

#                 successful.append(StudentCreatedResponse(
#                     id=user.id,
#                     firstname=user.firstname,
#                     lastname=user.lastname,
#                     phone=user.phone,
#                     role=user.role,
#                     org_id=user.org_id,
#                     is_first_login=user.is_first_login,
#                     access_code=access_code,
#                 ))

#             except HTTPException as e:
#                 errors.append({"row": row_num, "error": e.detail})
#             except Exception as e:
#                 errors.append({"row": row_num, "error": str(e)})

#         return BulkStudentResult(
#             total_rows=len(rows),
#             successful_rows=len(successful),
#             failed_rows=len(errors),
#             errors=errors,
#             students=successful,
#         )