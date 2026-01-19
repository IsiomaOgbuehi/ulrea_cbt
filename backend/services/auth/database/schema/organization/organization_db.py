from datetime import datetime, timezone
from sqlmodel import SQLModel, Field
from uuid import UUID, uuid4
from pydantic import EmailStr
from .enums import OrganizationType

class OrganizationBase(SQLModel):
    name: str = Field(index=True)
    verified: bool | None = False
    address: str | None = Field(default=None)
    email: EmailStr = Field(index=True)
    phone: str | None = Field(index=True)
    organization_type: OrganizationType
    updated_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )

class OrganizationModel(OrganizationBase, table=True):
    __tablename__ = 'organizations'
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    created_at: datetime | None = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )