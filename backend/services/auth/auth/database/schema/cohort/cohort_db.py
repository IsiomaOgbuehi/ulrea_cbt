from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field

from .enum import CohortStatus


class CohortModel(SQLModel, table=True):
    __tablename__ = "cohorts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    name: str = Field(index=True)               # e.g. "JSS3A", "2024 Intake"
    description: str | None = None
    created_by: UUID = Field(nullable=False)
    is_active: bool = Field(default=True)
    status: CohortStatus = Field(default=CohortStatus.ACTIVE, index=True)

    # Optional metadata — useful for schools and orgs alike
    academic_year: str | None = None            # "2024/2025" for schools
    start_date: datetime | None = None
    end_date: datetime | None = None            # expected graduation/completion date
    graduated_at: datetime | None = None        # actual graduation timestamp
    graduated_by: UUID | None = None

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CohortMember(SQLModel, table=True):
    __tablename__ = "cohort_members"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    cohort_id: UUID = Field(foreign_key="cohorts.id", index=True, nullable=False)
    student_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    added_by: UUID = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TeacherCohortAssignment(SQLModel, table=True):
    """
    Many-to-many: which teachers are assigned to which cohorts.
    A teacher may be assigned to several cohorts; a cohort may have
    several teachers (co-teaching / coverage).
    """
    __tablename__ = "teacher_cohort_assignments"
 
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    teacher_id: UUID = Field(foreign_key="users.id", index=True, nullable=False)
    cohort_id: UUID = Field(foreign_key="cohorts.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    assigned_by: UUID = Field(foreign_key="users.id", nullable=False)
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))