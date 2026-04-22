from sqlmodel import Field
from auth.database.schema.user.enums import UserRole
from pydantic import BaseModel, EmailStr

from .user_db import UserBase
from uuid import UUID
from datetime import datetime

class UserCreate(UserBase):
    password: str
    confirm_password: str

class UserRead(UserBase):
    id: UUID
    org_id: UUID
    verified: bool
    role: UserRole
    is_first_login: bool
    access_code: str | None = None  # only returned to creator, not on subsequent reads

class UserUpdate(UserBase):
    pass

class CreateStaffUser(UserBase):
    """Used by SUPER_ADMIN/ADMIN to create ADMIN/TEACHER/SUPERVISOR."""
    email: EmailStr = Field(index=True, unique=True)
    phone: str | None = None
    role: UserRole

class CreateStudent(UserBase):
    """Used by ADMIN/SUPER_ADMIN to create students."""
    pass
    # No email required, no password — access_code is generated

class StaffCreatedResponse(UserRead):
    temporary_password: str   # shown once — staff must change on first login


class StudentCreatedResponse(UserRead):
    access_code: str          # shown once — share with student


# First login flows
class StaffFirstLoginSetup(BaseModel):
    """Staff sets their permanent password on first login."""
    current_password: str     # the temporary one
    new_password: str
    confirm_new_password: str

class StudentFirstLoginSetup(BaseModel):
    """Student sets their favorite Q&A on first login."""
    access_code: str
    favorite_question: str
    favorite_answer: str

class StudentLoginRequest(BaseModel):
    access_code: str
    favorite_answer: str