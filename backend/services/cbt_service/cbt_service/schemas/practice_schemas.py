from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PracticeExamRead(BaseModel):
    """One of an exam body's exams in a student's External Exams."""
    exam_id: UUID
    title: str
    subject_name: str | None
    description: str | None
    duration_minutes: int
    question_count: int
    times_taken: int = 0
    best_percentage: float | None = None
    last_percentage: float | None = None
    last_taken_at: datetime | None = None
    in_progress_attempt_id: UUID | None = None     # resume this
    latest_attempt_id: UUID | None = None          # review the last finished one


class PracticeCatalogue(BaseModel):
    subscribed: bool
    expires_at: datetime | None
    exams: list[PracticeExamRead]


class PracticeStatsRead(BaseModel):
    """For the exam body: anonymous totals only (practice is personal)."""
    students: int
    attempts: int
    average_percentage: float | None
