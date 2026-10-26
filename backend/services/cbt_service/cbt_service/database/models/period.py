"""
Periods: when questions and exams are for, across the whole organisation.

Two levels, named by each organisation:
- a school might use Session > Term       (2025/2026 > First Term)
- a university Academic year > Semester   (2025/2026 > First Semester)
- a company Year > Review cycle           (2026 > Mid-year promotion)

A question or exam can be tagged with a top-level period (the whole session)
or with one of its children (a term). Filtering by a top-level period also
matches its children. Both are optional, so existing content is untouched.

OrgSettings holds organisation-wide preferences owned by this service, starting
with the period labels and the current period (which new questions and exams
default to).
"""
from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, Date, DateTime, String
from sqlmodel import Field, SQLModel

from .enums.item_subject_enums import PeriodStatus

DEFAULT_PERIOD_PARENT_LABEL = "Session"
DEFAULT_PERIOD_CHILD_LABEL = "Term"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Period(SQLModel, table=True):
    __tablename__ = "periods"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    # NULL = top level (session / year); set = a term / cycle inside it.
    parent_id: UUID | None = Field(default=None, foreign_key="periods.id", index=True)
    name: str = Field(nullable=False)
    status: str = Field(
        default=PeriodStatus.ACTIVE.value,
        sa_column=Column(String(20), nullable=False, server_default=PeriodStatus.ACTIVE.value),
    )
    starts_on: date | None = Field(default=None, sa_column=Column(Date, nullable=True))
    ends_on: date | None = Field(default=None, sa_column=Column(Date, nullable=True))
    position: int = Field(default=0)         # display order among siblings
    created_by: UUID = Field(nullable=False)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class OrgSettings(SQLModel, table=True):
    """One row per organisation, created on first save."""
    __tablename__ = "org_settings"

    org_id: UUID = Field(primary_key=True)
    period_parent_label: str = Field(
        default=DEFAULT_PERIOD_PARENT_LABEL, sa_column=Column(String(40), nullable=False)
    )
    period_child_label: str = Field(
        default=DEFAULT_PERIOD_CHILD_LABEL, sa_column=Column(String(40), nullable=False)
    )
    current_period_id: UUID | None = Field(default=None, foreign_key="periods.id")
    updated_by: UUID | None = None
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
