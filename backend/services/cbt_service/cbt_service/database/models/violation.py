from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import DateTime, JSON, Enum as SQLAlchemyEnum

from .enums.violation_enums import ViolationType, ViolationSeverity


class ExamViolation(SQLModel, table=True):
    __tablename__ = "exam_violations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)          # tenant isolation
    exam_id: UUID = Field(index=True, nullable=False)         # denormalized — avoids join for exam-level views
    attempt_id: UUID = Field(index=True, nullable=False, foreign_key="attempts.id")
    student_id: UUID = Field(index=True, nullable=False)      # denormalized from attempt for fast filtering

    violation_type: ViolationType = Field(
        sa_column=Column(
            SQLAlchemyEnum(ViolationType, values_callable=lambda obj: [e.value for e in obj]),
            nullable=False,
            index=True,
        )
    )
    severity: ViolationSeverity = Field(
        default=ViolationSeverity.LOW,
        sa_column=Column(
            SQLAlchemyEnum(ViolationSeverity, values_callable=lambda obj: [e.value for e in obj]),
            nullable=False,
        )
    )

    description: str | None = None          # human-readable note, e.g. "Switched tabs 3 times"
    metadata_: dict | None = Field(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True),
    )  # extensible bag for type-specific data: {"tab_count": 3} / {"face_count": 2} / {"blocked_key": "F12"}

    occurred_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    # client-reported time of the event itself — may differ from created_at by network lag
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )  # server receipt time — source of truth for ordering/audit

    reviewed: bool = Field(default=False)   # lets admin/teacher mark as "looked at" during review
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))