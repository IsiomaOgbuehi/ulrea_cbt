from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Optional
from sqlmodel import SQLModel, Field


class SubjectModel(SQLModel, table=True):
    __tablename__ = "subjects"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)            # tenant isolation
    name: str = Field(index=True)
    description: str | None = None
    status: str = Field(default="active")                       # active | archived
    created_by: UUID = Field(nullable=False)                    # user id of creator
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SubjectAssignment(SQLModel, table=True):
    """
    Tracks which staff members are assigned to a subject.
    Only assigned staff (+ admin/super_admin) can view/edit items in a subject.
    """
    __tablename__ = "subject_assignments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)            # denormalized for fast filtering
    assigned_to: UUID = Field(index=True, nullable=False)       # user id of staff
    assigned_by: UUID = Field(nullable=False)                   # user id of admin/super_admin
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
