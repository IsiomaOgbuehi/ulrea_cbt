from datetime import datetime, timezone
from uuid import UUID, uuid4
from pydantic import EmailStr
from sqlmodel import SQLModel, Field
from .enums import UserRole

class UserBase(SQLModel):
    firstname: str = Field(index=True)
    lastname: str
    othername: str | None
    email: EmailStr = Field(index=True)
    phone: str | None
    role: UserRole
    org_id: UUID = Field(
        foreign_key='organizations.id',
        nullable=False,
        index=True,
    )
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )
    verified: bool | None = False


class UserModel(UserBase, table=True):
    __tablename__ = 'users'
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )