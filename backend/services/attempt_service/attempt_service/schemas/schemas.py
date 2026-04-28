from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


class CurrentUser(BaseModel):
    id: UUID
    org_id: UUID
    role: str
    email: str | None = None
    verified: bool = False


class StartAttemptRequest(BaseModel):
    exam_id: UUID
    assignment_id: UUID


class SaveResponseRequest(BaseModel):
    item_id: UUID
    exam_item_id: UUID
    answer: list[str] | None = None
    time_spent_seconds: int = 0
    is_flagged: bool = False


class SubmitAttemptRequest(BaseModel):
    attempt_id: UUID


class ManualReviewRequest(BaseModel):
    response_id: UUID
    marks_awarded: float
    review_notes: str | None = None


class AttemptRead(BaseModel):
    id: UUID
    exam_id: UUID
    student_id: UUID
    status: str
    attempt_number: int
    started_at: datetime
    submitted_at: datetime | None
    raw_score: float | None
    final_score: float | None
    percentage: float | None
    passed: bool | None


class ResponseRead(BaseModel):
    id: UUID
    attempt_id: UUID
    item_id: UUID
    answer: list | None
    time_spent_seconds: int
    is_flagged: bool
    is_correct: bool | None
    marks_awarded: float | None