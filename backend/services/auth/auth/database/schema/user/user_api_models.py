from .user_db import UserBase
from uuid import UUID

class UserCreate(UserBase):
    password: str

class UserRead(UserBase):
    id: UUID

class UserUpdate(UserBase):
    pass