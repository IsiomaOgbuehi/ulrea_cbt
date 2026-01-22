from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import EmailStr
from sqlmodel import SQLModel, Field
from .enums import UserRole

class UserBase(SQLModel):
    firstname: str = Field(index=True)
    lastname: str
    othername: str | None = ''
    email: EmailStr = Field(index=True, unique=True)
    phone: str | None
    role: UserRole
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )


class UserModel(UserBase, table=True):
    __tablename__ = 'users'
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(
        foreign_key='organizations.id',
        nullable=False,
        index=True,
    )
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    password: str
    verified: bool | None = False