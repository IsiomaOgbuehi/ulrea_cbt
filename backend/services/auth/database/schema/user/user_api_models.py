from .user_db import UserBase
from uuid import UUID

class UsersGet(UserBase):
    id: UUID

class UserEdit(UserBase):
    pass