from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict

from cbt_service.database.models.enums.violation_enums import ViolationType, ViolationSeverity


class ViolationCreate(BaseModel):
    violation_type: ViolationType
    severity: ViolationSeverity = ViolationSeverity.LOW
    description: str | None = None
    metadata_: dict | None = None
    occurred_at: datetime


class ViolationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    exam_id: UUID
    attempt_id: UUID
    student_id: UUID
    violation_type: ViolationType
    severity: ViolationSeverity
    description: str | None
    metadata_: dict | None
    occurred_at: datetime
    created_at: datetime
    reviewed: bool
    reviewed_by: UUID | None
    reviewed_at: datetime | None


class ViolationCreateResult(BaseModel):
    violation: ViolationRead
    violation_count: int
    max_violations: int | None
    exam_terminated: bool