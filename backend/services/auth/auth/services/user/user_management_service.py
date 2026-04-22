import secrets
import string
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import select
from auth.database.schema.user.enums import UserRole
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.user_api_models import CreateStaffUser, CreateStudent
from auth.utility.password.password_harsher import PasswordHasher


# Who can create whom
CREATION_PERMISSIONS: dict[UserRole, list[UserRole]] = {
    UserRole.SUPER_ADMIN: [UserRole.ADMIN, UserRole.TEACHER, UserRole.SUPERVISOR, UserRole.STUDENT],
    UserRole.ADMIN: [UserRole.TEACHER, UserRole.SUPERVISOR, UserRole.STUDENT],
}

class UserManagementService:

    @staticmethod
    def _generate_access_code(length: int = 6) -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))
    
    staticmethod
    def _generate_temp_password(length: int = 12) -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$"
        return "".join(secrets.choice(alphabet) for _ in range(length))
    
    @classmethod
    def create_staff(
        cls,
        session: SessionDep,
        creator: UserModel,
        payload: CreateStaffUser,
        org_id: UUID,
    ) -> tuple[UserModel, str]:
        """Returns (user, temporary_password). Caller must send temp password to user via email."""

        allowed = CREATION_PERMISSIONS.get(creator.role, [])
        if payload.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"{creator.role} cannot create users with role {payload.role}."
            )

        existing = session.exec(
            select(UserModel).where(UserModel.email == payload.email)
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="A user with this email already exists.")

        temp_password = cls._generate_temp_password()

        user = UserModel(
            firstname=payload.firstname,
            lastname=payload.lastname,
            othername=payload.othername or "",
            email=payload.email,
            phone=payload.phone,
            role=payload.role,
            org_id=org_id,
            password=PasswordHasher.create(temp_password),
            verified=False,
            is_first_login=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        return user, temp_password
    

    @classmethod
    def create_student(
        cls,
        session: SessionDep,
        creator: UserModel,
        payload: CreateStudent,
        org_id: UUID,
    ) -> tuple[UserModel, str]:
        """Returns (user, access_code). Caller must share access_code with student."""

        allowed = CREATION_PERMISSIONS.get(creator.role, [])
        if UserRole.STUDENT not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"{creator.role} cannot create students."
            )

        # Ensure access code is unique
        while True:
            access_code = cls._generate_access_code()
            existing = session.exec(
                select(UserModel).where(UserModel.access_code == access_code)
            ).first()
            if not existing:
                break

        user = UserModel(
            firstname=payload.firstname,
            lastname=payload.lastname,
            othername=payload.othername or "",
            phone=payload.phone,
            role=UserRole.STUDENT,
            org_id=org_id,
            access_code=access_code,
            verified=False,
            is_first_login=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        return user, access_code