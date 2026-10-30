from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON, DateTime, UniqueConstraint

from .enums.attempt_enum import AttemptStatus


from sqlalchemy import Column, DateTime

class AttemptModel(SQLModel, table=True):
    """
    One student's attempt at one exam.
    Respects max_attempts from ExamModel.
    """
    __tablename__ = "attempts"
    # One row per attempt number per student per exam: the database itself
    # refuses a duplicate if two "start" requests ever race.
    __table_args__ = (
        UniqueConstraint("exam_id", "student_id", "attempt_number", name="uq_attempts_exam_student_number"),
    )

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
    # When the exam clock started: the moment the questions were first sent
    # to the student, not when they pressed "Begin" (on a slow connection the
    # page can take a while to load). None until then.
    clock_started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )

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
    reviewed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )

    answered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False),)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=False),)


class LateResponseModel(SQLModel, table=True):
    """
    An answer that reached the server after the attempt's time ran out
    (typically held up by a bad connection). It never changes the score by
    itself: staff see when it arrived and decide whether to accept it.
    """
    __tablename__ = "late_responses"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    attempt_id: UUID = Field(foreign_key="attempts.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    item_id: UUID = Field(nullable=False)
    exam_item_id: UUID = Field(nullable=False)
    answer: list[str] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    received_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    seconds_late: float = Field(default=0.0)
    # The attempt's last on-time save: a steady stream of saves until the
    # deadline, then silence, points to a dropped connection.
    last_on_time_save_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    status: str = Field(default="pending", index=True)     # pending | accepted | rejected
    decided_by: UUID | None = None
    decided_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
