from sqlmodel import Field
from auth.database.schema.user.enums import UserRole
from pydantic import BaseModel, EmailStr

from .token import TokenBase

from ..database.schema.user.user_db import UserBase
from uuid import UUID
from datetime import datetime

# ============================================================
# SIGNUP — super admin creating their org account
# ============================================================

class UserSignupCreate(BaseModel):
    """Only what's needed for self-service signup."""
    firstname: str
    lastname: str
    othername: str | None = None
    email: EmailStr
    phone: str | None = None
    password: str
    confirm_password: str

class UserRead(UserBase):
    id: UUID
    org_id: UUID
    verified: bool
    role: UserRole
    is_first_login: bool
    access_code: str | None = None  # only returned to creator, not on subsequent reads

class UserBaseResponse(BaseModel):
    id: UUID
    org_id: UUID
    verified: bool
    role: UserRole

class StaffUserResponse(UserBaseResponse):
    email: EmailStr
    firstname: str
    lastname: str

class UserUpdate(UserBase):
    pass

class CreateStaffUser(BaseModel):
    """Used by SUPER_ADMIN/ADMIN to create ADMIN/TEACHER/SUPERVISOR."""
    firstname: str
    lastname: str
    othername: str | None = None
    email: EmailStr
    phone: str | None = None
    role: UserRole
    institution_id: str | None = None

class CreateStudent(BaseModel):
    """Used by ADMIN/SUPER_ADMIN to create students."""
    firstname: str
    lastname: str
    othername: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    institution_id: str | None = None  # e.g. student reg number
    # No email required, no password — access_code is generated

class StaffCreatedResponse(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    email: str
    role: UserRole
    org_id: UUID
    is_first_login: bool
    temporary_password: str       # shown once only


class StudentCreatedResponse(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    phone: str | None
    role: UserRole
    org_id: UUID
    is_first_login: bool
    access_code: str              # shown once only, share with student


# First login flows
class StaffFirstLoginSetup(BaseModel):
    """Staff sets their permanent password on first login."""
    email: EmailStr
    current_password: str     # the temporary one
    new_password: str
    confirm_new_password: str

class StudentFirstLoginSetup(BaseModel):
    """Student sets their favorite Q&A on first login."""
    access_code: str
    favorite_question: str
    favorite_answer: str

class StudentAccessCodeRequest(BaseModel):
    access_code: str

class StudentLoginRequest(BaseModel):
    access_code: str
    favorite_answer: str

class StaffActivationPayload(BaseModel):
    token: str
    password: str
    confirm_password: str

class StudentLoginUserResponse(BaseModel):
    firstname: str
    lastname: str
    othername: str | None = None
    institution_id: str | None = None
    id: UUID
    org_id: UUID
    verified: bool
    role: UserRole

class StudentLoginResponse(TokenBase):
    user: StudentLoginUserResponse