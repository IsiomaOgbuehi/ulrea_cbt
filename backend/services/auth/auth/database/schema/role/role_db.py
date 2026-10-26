"""
Roles an organisation can edit.

Built-in roles (admin, teacher, supervisor, staff) need no row: their
permissions come from the code defaults until an admin edits one, which saves
a row keyed by the built-in name. "Reset to default" clears it again, so
improvements to the defaults reach every organisation that hasn't customised.

Custom roles (e.g. "HR Officer") are rows with their own permissions. Members
holding one keep the base role "staff" on their membership, and point to the
row through org_memberships.role_id.

The owner (super_admin) and candidates (student) are fixed and never stored.
"""
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, DateTime, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrgRole(SQLModel, table=True):
    __tablename__ = "org_roles"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_roles_org_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(foreign_key="organizations.id", index=True, nullable=False)
    # Built-in roles use their name ("teacher"); custom roles a slug of theirs.
    key: str = Field(sa_column=Column(String(60), nullable=False))
    name: str = Field(sa_column=Column(String(80), nullable=False))
    description: str | None = None
    # The fixed role members get on their membership (custom roles: "staff").
    base_role: str = Field(sa_column=Column(String(20), nullable=False))
    is_system: bool = Field(default=False)
    # {"exams.view": "assigned", ...}; NULL on a built-in role = code defaults.
    permissions: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_by: UUID | None = None
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
