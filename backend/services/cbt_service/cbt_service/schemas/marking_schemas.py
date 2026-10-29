"""Marking written answers (short answer / essay): the per-exam queue."""
from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from cbt_service.schemas.attempt_schemas import ResponseRead
from cbt_service.schemas.item_subject_schemas import PaginatedResponse


class MarkingFilter(str, Enum):
    PENDING = "pending"      # waiting for a person
    MARKED = "marked"        # marked by a person
    AUTO = "auto"            # marked automatically (accepted answer matched / blank)
    ALL = "all"


class ScriptFilter(str, Enum):
    PENDING = "pending"      # at least one answer waiting for a person
    DONE = "done"            # every written answer has marks
    ALL = "all"


class MarkingQuestionInfo(BaseModel):
    """A written question as the marker sees it."""
    exam_item_id: UUID
    item_id: UUID
    item_type: str
    question_text: str
    marks: float                         # the exam's marks for it
    marking_guide: str | None = None
    accepted_answers: list[str] | None = None   # short answers
    word_limit: int | None = None


class MarkingQuestion(MarkingQuestionInfo):
    answered: int = 0                    # submitted answers
    pending: int = 0
    marked: int = 0
    auto_marked: int = 0


class MarkingOverview(BaseModel):
    exam_id: UUID
    title: str
    total_marks: float
    auto_mark_short_answers: bool = False
    submitted_attempts: int
    attempts_waiting: int                # attempts with at least one answer to mark
    pending: int
    marked: int
    questions: list[MarkingQuestion]


class MarkingAnswer(BaseModel):
    response_id: UUID
    attempt_id: UUID
    student_id: UUID
    student_name: str | None = None
    answer: list[str] | None = None
    word_count: int = 0
    marks_awarded: float | None = None
    is_correct: bool | None = None
    review_notes: str | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    auto_marked: bool = False
    # Short answers: matches one of the accepted answers (a hint for the marker).
    matches_accepted: bool | None = None
    submitted_at: datetime | None = None


class ReviewResult(ResponseRead):
    """A marked answer, with the attempt's totals after re-scoring."""
    attempt_final_score: float | None = None
    attempt_percentage: float | None = None
    attempt_passed: bool | None = None
    attempt_pending: int = 0             # answers in the attempt still to mark


class MarkingScript(BaseModel):
    """One student's submitted attempt, for marking student by student."""
    attempt_id: UUID
    student_id: UUID
    student_name: str | None = None
    student_number: str | None = None    # the org's number for the student, if set
    number: int = 0                      # "Script N": position in submission order (stable for blind marking)
    attempt_number: int = 1
    status: str
    submitted_at: datetime | None = None
    final_score: float | None = None
    percentage: float | None = None
    passed: bool | None = None
    written: int = 0                     # written answers in the script
    pending: int = 0                     # waiting for a person
    marked: int = 0                      # marked by a person
    auto_marked: int = 0


class MarkingScriptEntry(BaseModel):
    question: MarkingQuestionInfo
    answer: MarkingAnswer | None = None  # None: the student left it unanswered


class MarkingScriptDetail(MarkingScript):
    total_marks: float
    entries: list[MarkingScriptEntry]


class ScriptCounts(BaseModel):
    """Scripts per filter, for the whole exam (ignores search and paging)."""
    pending: int = 0
    done: int = 0
    all: int = 0


class MarkingScriptPage(PaginatedResponse[MarkingScript]):
    counts: ScriptCounts
