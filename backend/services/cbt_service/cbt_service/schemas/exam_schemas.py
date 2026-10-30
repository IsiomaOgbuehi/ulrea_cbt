from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator
from uuid import UUID
from datetime import datetime
from typing import Any
from cbt_service.database.models.enums.exam_enum import ExamAction, ExamStatus, AssignmentStatus
from cbt_service.database.models.enums.enums import UserRole


class ExamCreate(BaseModel):
    title: str
    subject_id: UUID
    description: str | None = None
    instructions: str | None = None
    duration_minutes: int = 60
    pass_mark: float | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    shuffle_questions: bool = False
    shuffle_options: bool = False
    show_result_immediately: bool = True
    allow_review: bool = True
    auto_mark_short_answers: bool = False   # off: short answers are marked by a person
    show_attempts_used: bool = False        # candidates see "1 of 2 attempts used"
    extra_time_seconds: int = Field(default=30, ge=0, le=1800)   # added to every candidate's time
    max_attempts: int = 1
    max_violations: int | None = None
    # Optional scope inside the subject. Empty = whole subject (as before).
    bank_id: UUID | None = None
    topic_ids: list[UUID] = Field(default_factory=list)
    period_id: UUID | None = None          # session/term (year/cycle...) the exam is for

    @model_validator(mode="after")
    def validate_times(self):
        if self.start_time and self.end_time:
            if self.end_time <= self.start_time:
                raise ValueError("end_time must be after start_time")
        return self


class ExamMaxAttemptsUpdate(BaseModel):
    """
    Change how many attempts candidates get, and whether they see how many
    they've used, once an exam is approved or live. Send either or both.
    """
    max_attempts: int | None = Field(default=None, ge=1, le=100)
    show_attempts_used: bool | None = None
    # Applies straight away, including to candidates already sitting the exam.
    extra_time_seconds: int | None = Field(default=None, ge=0, le=1800)

    @model_validator(mode="after")
    def something_to_change(self) -> "ExamMaxAttemptsUpdate":
        if self.max_attempts is None and self.show_attempts_used is None and self.extra_time_seconds is None:
            raise ValueError("Send max_attempts, show_attempts_used or extra_time_seconds.")
        return self


class ExamShowResultsUpdate(BaseModel):
    """Show candidates their results (or hide them again) once an exam is approved, live or closed."""
    show_result_immediately: bool


class ExamUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    instructions: str | None = None
    duration_minutes: int | None = None
    pass_mark: float | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    shuffle_questions: bool | None = None
    shuffle_options: bool | None = None
    show_result_immediately: bool | None = None
    allow_review: bool | None = None
    auto_mark_short_answers: bool | None = None   # only while the exam is a draft / sent back
    show_attempts_used: bool | None = None
    extra_time_seconds: int | None = Field(default=None, ge=0, le=1800)
    max_attempts: int | None = None
    max_violations: int | None = None
    bank_id: UUID | None = None            # send null to clear
    topic_ids: list[UUID] | None = None    # replaces the exam's topics when sent
    period_id: UUID | None = None          # send null to clear


class ExamRead(BaseModel):
    id: UUID
    org_id: UUID
    subject_id: UUID
    subject_name: str | None = None
    created_by: UUID
    approved_by: UUID | None
    title: str
    description: str | None
    instructions: str | None
    duration_minutes: int
    pass_mark: float | None
    total_marks: float
    status: ExamStatus
    rejection_reason: str | None
    start_time: datetime | None
    end_time: datetime | None
    shuffle_questions: bool
    shuffle_options: bool
    show_result_immediately: bool
    allow_review: bool
    auto_mark_short_answers: bool = False
    show_attempts_used: bool = False
    extra_time_seconds: int = 30
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    bank_id: UUID | None = None
    topic_ids: list[UUID] = Field(default_factory=list)
    period_id: UUID | None = None
    item_count: int = 0          # questions in the exam
    written_count: int = 0                 # short answers + essays (the Marking tab shows when > 0)
    short_answer_count: int = 0
    assigned_count: int = 0      # candidates assigned
    submitted_count: int = 0     # of those, how many have submitted


class ExamPhase(str, Enum):
    """Where an exam is in its life (from status + window); used to group the exams list."""
    UPCOMING = "upcoming"
    LIVE = "live"
    PAST = "past"
    DRAFTS = "drafts"


class ExamPhaseCounts(BaseModel):
    upcoming: int
    live: int
    past: int
    drafts: int
    all: int


class ExamSectionCreate(BaseModel):
    title: str
    instructions: str | None = None
    time_limit_minutes: int | None = None
    order: int = 0


class ExamSectionRead(BaseModel):
    id: UUID
    exam_id: UUID
    title: str
    instructions: str | None
    time_limit_minutes: int | None
    order: int


class ExamItemAdd(BaseModel):
    item_ids: list[UUID]
    section_id: UUID | None = None


class ExamItemRead(BaseModel):
    id: UUID
    exam_id: UUID
    section_id: UUID | None
    item_id: UUID
    order: int
    marks: float
    negative_marks: float


class ApprovalAction(BaseModel):
    action: ExamStatus                         # "approve" | "reject"
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def validate_rejection(self):
        if self.action == ExamStatus.REJECTED and not self.rejection_reason:
            raise ValueError("rejection_reason is required when rejecting an exam")
        if self.action not in (ExamStatus.APPROVED, ExamStatus.REJECTED):
            raise ValueError("action must be 'approve' or 'reject'")
        return self


class AssignStudentsRequest(BaseModel):
    student_ids: list[UUID]
    scheduled_at: datetime | None = None


class ExamAssignmentRead(BaseModel):
    id: UUID
    student_name: str | None = None
    exam_id: UUID
    student_id: UUID
    org_id: UUID
    assigned_by: UUID
    status: AssignmentStatus
    scheduled_at: datetime | None
    created_at: datetime


# Names stored before the column became plain text (e.g. "CREATED", "ARCHIVED").
_LEGACY_ACTIONS: dict[str, ExamAction] = {"ARCHIVED": ExamAction.EXAM_ARCHIVED}


class ExamAuditLogRead(BaseModel):
    id: UUID
    exam_id: UUID
    actor_id: UUID
    # Known actions come back as their value ("created"); anything unrecognised
    # is passed through as text rather than failing the whole log.
    action: ExamAction | str
    extra_data: dict | None
    created_at: datetime

    @field_validator("action", mode="before")
    @classmethod
    def _normalise_action(cls, raw: object) -> ExamAction | str:
        """Old rows hold the enum NAME ("CREATED"); newer ones the value ("created")."""
        if isinstance(raw, ExamAction):
            return raw
        text: str = str(raw).strip()
        if text in _LEGACY_ACTIONS:
            return _LEGACY_ACTIONS[text]
        try:
            return ExamAction(text.lower())
        except ValueError:
            pass
        member: ExamAction | None = ExamAction.__members__.get(text.upper())
        return member if member is not None else text.lower()


# One CurrentUser for the whole service (see item_subject_schemas).
from cbt_service.schemas.current_user import CurrentUser as CurrentUser  # noqa: E402


class AssignCohortRequest(BaseModel):
    cohort_id: UUID
    scheduled_at: datetime | None = None


class UserSummary(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    email: str | None
    role: UserRole




class ExamItemInternalRead(BaseModel):
    id: UUID | None
    item_id: UUID
    order: int
    marks: float  # exam-level override, if you support per-exam marks overrides

class ExamSectionInternalRead(BaseModel):
    id: UUID
    title: str
    order: int
    items: list[ExamItemInternalRead]


class MyAssignmentRead(BaseModel):
    assignment_id: UUID
    exam_id: UUID
    exam_title: str
    status: AssignmentStatus            # assignment status: pending / scheduled / etc.
    scheduled_at: datetime | None
    duration_minutes: int
    extra_time_seconds: int = 0
    effective_start: datetime | None   # ← new
    effective_end: datetime | None     # ← new
    can_attempt: bool                  # ← new — computed server-side, frontend just reads it
    start_time: datetime | None
    end_time: datetime | None
    has_attempted: bool     # so the frontend can show "resume" vs "start"
    # Attempts, worked out from the student's attempts (not the assignment status).
    # Both left out (None) unless the exam shows candidates their attempts used.
    max_attempts: int | None = None
    attempts_used: int | None = None            # attempts started or finished (resets don't count)
    show_attempts_used: bool = False
    in_progress_attempt_id: UUID | None = None  # an attempt to resume (started, or reset by staff)
    restart: bool = False                       # that attempt was reset by staff: it starts afresh
    latest_attempt_id: UUID | None = None       # the latest finished attempt, for "View result"
    can_retake: bool = False                    # finished before, attempts left, window open
    show_result: bool = True
    allow_review: bool = False


class ExamStatusUpdate(BaseModel):
    status: ExamStatus
    reason: str | None = None  # optional context, useful for rejections/archives via this override


class ExamDeleteResponse(BaseModel):
    detail: str
    exam_id: UUID
    action: str  # "deleted" | "archived"