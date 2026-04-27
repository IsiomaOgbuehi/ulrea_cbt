from pydantic import BaseModel, model_validator
from uuid import UUID
from datetime import datetime
from typing import Any
from exam_service.database.models.enums import ExamStatus, AssignmentStatus


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


class ExamRead(BaseModel):
    id: UUID
    org_id: UUID
    subject_id: UUID
    created_by: UUID
    approved_by: UUID | None
    title: str
    description: str | None
    instructions: str | None
    duration_minutes: int
    pass_mark: float | None
    total_marks: float
    status: str
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
    action: str                         # "approve" | "reject"
    rejection_reason: str | None = None

    @model_validator(mode="after")
    def validate_rejection(self):
        if self.action == "reject" and not self.rejection_reason:
            raise ValueError("rejection_reason is required when rejecting an exam")
        if self.action not in ("approve", "reject"):
            raise ValueError("action must be 'approve' or 'reject'")
        return self


class AssignStudentsRequest(BaseModel):
    student_ids: list[UUID]
    scheduled_at: datetime | None = None


class ExamAssignmentRead(BaseModel):
    id: UUID
    exam_id: UUID
    student_id: UUID
    org_id: UUID
    assigned_by: UUID
    status: str
    scheduled_at: datetime | None
    created_at: datetime


class ExamAuditLogRead(BaseModel):
    id: UUID
    exam_id: UUID
    actor_id: UUID
    action: str
    extra_data: dict | None
    created_at: datetime


class CurrentUser(BaseModel):
    id: UUID
    org_id: UUID
    role: str
    email: str | None = None
    verified: bool = False
