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
    max_attempts: int
    created_at: datetime
    updated_at: datetime
    bank_id: UUID | None = None
    topic_ids: list[UUID] = Field(default_factory=list)
    period_id: UUID | None = None
    item_count: int = 0          # questions in the exam
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
    effective_start: datetime | None   # ← new
    effective_end: datetime | None     # ← new
    can_attempt: bool                  # ← new — computed server-side, frontend just reads it
    start_time: datetime | None
    end_time: datetime | None
    has_attempted: bool     # so the frontend can show "resume" vs "start"


class ExamStatusUpdate(BaseModel):
    status: ExamStatus
    reason: str | None = None  # optional context, useful for rejections/archives via this override


class ExamDeleteResponse(BaseModel):
    detail: str
    exam_id: UUID
    action: str  # "deleted" | "archived"