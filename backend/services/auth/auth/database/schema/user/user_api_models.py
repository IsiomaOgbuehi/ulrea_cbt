from .user_db import UserBase
from uuid import UUID
from datetime import datetime

class UserCreate(UserBase):
    password: str
    confirm_password: str

class UserRead(UserBase):
    id: UUID
    org_id: UUID

class UserUpdate(UserBase):
    pass