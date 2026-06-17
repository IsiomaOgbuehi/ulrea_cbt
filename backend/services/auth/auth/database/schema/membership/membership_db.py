from sqlmodel import SQLModel, Field
from uuid import UUID, uuid4
from datetime import datetime, timezone
from auth.database.schema.user.enums import UserRole, MembershipStatus, VerificationMethod
from auth.database.schema.membership.enum import MembershipJoinType


class OrgMembership(SQLModel, table=True):
    __tablename__ = "org_memberships"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    org_id: UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    role: UserRole = Field(nullable=False)
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE, index=True)
    join_type: MembershipJoinType = Field(default=MembershipJoinType.INVITED, index=True)
    institution_id: str | None = Field(default=None)
    verification_method: VerificationMethod | None = Field(default=None)
    created_by: UUID | None = Field(default=None)       # None for self-joined
    archived_by: UUID | None = Field(default=None)
    archived_at: datetime | None = Field(default=None)
    archive_reason: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))