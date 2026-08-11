from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON, DateTime

from .enums.attempt_enum import AttemptStatus


from sqlalchemy import Column, DateTime

class AttemptModel(SQLModel, table=True):
    """
    One student's attempt at one exam.
    Respects max_attempts from ExamModel.
    """
    __tablename__ = "attempts"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    exam_id: UUID = Field(index=True, nullable=False)
    student_id: UUID = Field(index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    assignment_id: UUID = Field(index=True, nullable=False)

    status: AttemptStatus = Field(default=AttemptStatus.STARTED, index=True)
    attempt_number: int = Field(default=1)

    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    submitted_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    time_remaining_seconds: int | None = None

    raw_score: float | None = None
    final_score: float | None = None
    percentage: float | None = None
    passed: bool | None = None
    scored_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    scored_by: str | None = None


class ResponseModel(SQLModel, table=True):
    """
    A student's answer to a single item within an attempt.
    Autosaved continuously during exam.
    """
    __tablename__ = "responses"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    attempt_id: UUID = Field(foreign_key="attempts.id", index=True, nullable=False)
    item_id: UUID = Field(index=True, nullable=False)           # from item bank
    exam_item_id: UUID = Field(nullable=False)                  # exam_items.id
    org_id: UUID = Field(index=True, nullable=False)

    answer: list | None = Field(default=None, sa_column=Column(JSON))  # student's selected answers
    time_spent_seconds: int = Field(default=0)
    is_flagged: bool = Field(default=False)                     # student flagged for review

    # Scoring
    is_correct: bool | None = None
    marks_awarded: float | None = None
    reviewed_by: UUID | None = None                             # for manual review
    review_notes: str | None = None

    answered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False),)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False),)
