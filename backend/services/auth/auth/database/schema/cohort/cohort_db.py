from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, DateTime, Index, String, UniqueConstraint, text

from .enum import CohortStatus, MemberStatus


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

    # One active membership per student per cohort. Graduated / left rows are
    # history, so a student who left can join the same cohort again.
    __table_args__ = (
        Index(
            "uq_cohort_member_active",
            "cohort_id", "student_id", "org_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    cohort_id: UUID = Field(foreign_key="cohorts.id", index=True, nullable=False)
    student_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False
    )
    org_id: UUID = Field(index=True, nullable=False)
    added_by: UUID = Field(nullable=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Stored as a plain string (validated by MemberStatus) to avoid enum migrations.
    status: str = Field(
        default=MemberStatus.ACTIVE.value,
        sa_column=Column(String(20), nullable=False, server_default=MemberStatus.ACTIVE.value, index=True),
    )
    ended_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    ended_by: UUID | None = None
    end_note: str | None = None     # e.g. "2024/2025 session" or "Moved to JSS3A"


class TeacherCohortAssignment(SQLModel, table=True):
    """
    Many-to-many: which teachers are assigned to which cohorts.
    A teacher may be assigned to several cohorts; a cohort may have
    several teachers (co-teaching / coverage).
    """
    __tablename__ = "teacher_cohort_assignments"
 
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    teacher_id: UUID = Field(
        foreign_key="users.id", ondelete="CASCADE", index=True, nullable=False
    )
    cohort_id: UUID = Field(foreign_key="cohorts.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    assigned_by: UUID | None = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL", nullable=True
    )
    assigned_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))