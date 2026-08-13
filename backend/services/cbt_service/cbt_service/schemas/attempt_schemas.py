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
    expires_at: datetime          # ← add
    remaining_seconds: int        # ← add
    server_time: datetime         # ← add


class ResponseRead(BaseModel):
    id: UUID
    attempt_id: UUID
    item_id: UUID
    answer: list | None
    time_spent_seconds: int
    is_flagged: bool
    is_correct: bool | None
    marks_awarded: float | None



class AttemptOptionRead(BaseModel):
    key: str
    text: str

class AttemptItemRead(BaseModel):
    id: UUID
    exam_item_id: UUID
    question_text: str
    item_type: str
    options: list[AttemptOptionRead] | None = None   # for MCQ — no correct_answers field at all
    marks: float

class AttemptSectionRead(BaseModel):
    id: UUID
    title: str
    items: list[AttemptItemRead]

class AttemptExamRead(BaseModel):
    attempt_id: UUID
    exam_id: UUID
    duration_minutes: int
    shuffle_questions: bool
    shuffle_options: bool
    sections: list[AttemptSectionRead]



class AttemptResponseDetail(BaseModel):
    item_id: UUID
    question_text: str
    answer: list[str] | None
    correct_answers: list[str] | None   # only shown to teacher/admin
    is_correct: bool | None
    marks_awarded: float | None


class AttemptDetailRead(BaseModel):
    id: UUID
    student_id: UUID
    exam_id: UUID
    status: str
    started_at: datetime
    submitted_at: datetime | None
    raw_score: float | None
    final_score: float | None
    percentage: float | None
    passed: bool | None
    responses: list[AttemptResponseDetail]


class CohortAttemptSummary(BaseModel):
    id: UUID
    student_id: UUID
    status: str
    submitted_at: datetime | None
    final_score: float | None
    percentage: float | None
    passed: bool | None


class ResetAttemptRequest(BaseModel):
    reason: str
    new_scheduled_at: datetime | None = None  # if rescheduling, not just voiding