from typing import Annotated

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

from cbt_service.database.models.enums.attempt_enum import AttemptStatus


# One CurrentUser for the whole service (see item_subject_schemas).
from cbt_service.schemas.current_user import CurrentUser as CurrentUser  # noqa: E402


class StartAttemptRequest(BaseModel):
    exam_id: UUID
    assignment_id: UUID


class SaveResponseRequest(BaseModel):
    item_id: UUID
    exam_item_id: UUID
    # Essays are rich text, so allow long entries, but not unbounded ones.
    answer: list[Annotated[str, Field(max_length=100_000)]] | None = Field(default=None, max_length=20)
    time_spent_seconds: int = 0
    is_flagged: bool = False


class SubmitAttemptRequest(BaseModel):
    attempt_id: UUID


class ManualReviewRequest(BaseModel):
    response_id: UUID | None = None          # optional: it's in the URL
    marks_awarded: float
    review_notes: str | None = Field(default=None, max_length=5_000)


class AttemptRead(BaseModel):
    id: UUID
    exam_id: UUID
    assignment_id: UUID | None = None
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
    # The exam's settings, so the student's screens follow them. When results
    # aren't shown, scores and pass/fail come back empty to the student.
    show_result: bool = True
    allow_review: bool = False
    # Written answers still to be marked by a person. While > 0 the score is
    # provisional and passed is None.
    awaiting_marking: int = 0


class ResponseRead(BaseModel):
    id: UUID
    attempt_id: UUID
    item_id: UUID
    answer: list | None
    time_spent_seconds: int
    is_flagged: bool
    is_correct: bool | None
    marks_awarded: float | None
    review_notes: str | None = None
    reviewed_at: datetime | None = None



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
    word_limit: int | None = None                    # essays

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
    response_id: UUID | None = None
    exam_item_id: UUID | None = None
    item_type: str | None = None
    question_text: str
    answer: list[str] | None
    correct_answers: list[str] | None   # only shown to teacher/admin
    is_correct: bool | None
    marks_awarded: float | None
    max_marks: float | None = None
    marking_guide: str | None = None    # staff view only
    review_notes: str | None = None
    reviewed_at: datetime | None = None


class LateAnswerRead(BaseModel):
    """An answer that arrived after time ran out, for staff to accept or reject."""
    id: UUID
    item_id: UUID
    exam_item_id: UUID
    question_text: str
    item_type: str | None = None
    answer: list[str] | None = None
    current_answer: list[str] | None = None   # what was saved on time, if anything
    received_at: datetime
    seconds_late: float
    last_on_time_save_at: datetime | None = None
    status: str
    decided_at: datetime | None = None


class LateAnswerDecision(BaseModel):
    accept: bool


class LateSaveRead(BaseModel):
    """Reply to a save that arrived after time ran out."""
    held_for_review: bool = True
    detail: str = "Time was up when this answer arrived. It has been kept for your teacher to review."


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
    awaiting_marking: int = 0
    responses: list[AttemptResponseDetail]
    late_answers: list[LateAnswerRead] = []


class CohortAttemptSummary(BaseModel):
    id: UUID
    student_id: UUID
    student_name: str | None = None
    status: str
    submitted_at: datetime | None
    final_score: float | None
    percentage: float | None
    passed: bool | None
    awaiting_marking: int = 0
    late_answers: int = 0          # late answers waiting for a decision


class ResetAttemptRequest(BaseModel):
    reason: str
    new_scheduled_at: datetime | None = None  # if rescheduling, not just voiding


class AttemptStatusRead(BaseModel):
    status: AttemptStatus
    remaining_seconds: int
    violation_count: int
    max_violations: int | None
    server_time: datetime

class ReviewItem(BaseModel):
    """One question in a student's own review of a finished attempt."""
    item_id: UUID
    question_text: str
    item_type: str
    options: list[AttemptOptionRead] | None = None
    answer: list[str] | None = None          # None: not answered
    marks: float
    # Only when the exam shows results:
    marks_awarded: float | None = None
    is_correct: bool | None = None
    correct_answers: list[str] | None = None  # objective questions only
    review_notes: str | None = None
    awaiting_marking: bool = False


class AttemptReviewRead(BaseModel):
    attempt_id: UUID
    exam_id: UUID
    exam_title: str
    show_result: bool
    total_marks: float
    final_score: float | None = None
    percentage: float | None = None
    passed: bool | None = None
    awaiting_marking: int = 0
    items: list[ReviewItem]
