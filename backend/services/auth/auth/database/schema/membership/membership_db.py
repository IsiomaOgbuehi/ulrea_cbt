from sqlalchemy import Index, text
from sqlmodel import SQLModel, Field
from uuid import UUID, uuid4
from datetime import datetime, timezone
from auth.database.schema.user.enums import UserRole, MembershipStatus, VerificationMethod
from auth.database.schema.membership.enum import MembershipJoinType


class OrgMembership(SQLModel, table=True):
    __tablename__ = "org_memberships"
    __table_args__ = (
        # Institution IDs Juwazi generates are unique across every organisation.
        Index(
            "uq_org_memberships_generated_institution_id", "institution_id",
            unique=True,
            postgresql_where=text("institution_id_generated"),
            sqlite_where=text("institution_id_generated"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    org_id: UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    role: UserRole = Field(nullable=False)
    # Set when the member holds a custom role (role is then "staff"); NULL
    # means the built-in role named by `role`.
    role_id: UUID | None = Field(default=None, foreign_key="org_roles.id", index=True)
    status: MembershipStatus = Field(default=MembershipStatus.ACTIVE, index=True)
    join_type: MembershipJoinType = Field(default=MembershipJoinType.INVITED, index=True)
    institution_id: str | None = Field(default=None)
    # Made by Juwazi (JW-XXXXXXXX) because none was given: unique everywhere.
    institution_id_generated: bool = Field(default=False)
    verification_method: VerificationMethod | None = Field(default=None)
    created_by: UUID | None = Field(default=None)       # None for self-joined
    archived_by: UUID | None = Field(default=None)
    archived_at: datetime | None = Field(default=None)
    archive_reason: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))