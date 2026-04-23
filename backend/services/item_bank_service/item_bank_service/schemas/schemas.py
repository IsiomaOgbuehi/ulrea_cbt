from pydantic import BaseModel, field_validator
from uuid import UUID
from datetime import datetime
from typing import Any
from item_bank_service.database.models.enums import ItemType, ItemStatus, SubjectStatus


# ============================================================
# SUBJECT SCHEMAS
# ============================================================

class SubjectCreate(BaseModel):
    name: str
    description: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: SubjectStatus | None = None


class SubjectRead(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    description: str | None
    status: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class SubjectAssignRequest(BaseModel):
    user_ids: list[UUID]    # assign multiple staff at once


class SubjectAssignmentRead(BaseModel):
    id: UUID
    subject_id: UUID
    assigned_to: UUID
    assigned_by: UUID
    created_at: datetime


# ============================================================
# ITEM SCHEMAS
# ============================================================

class ItemCreate(BaseModel):
    stem: str
    type: ItemType
    options: list[str] | None = None
    correct_answer: list[str] | None = None
    explanation: str | None = None
    marks: float = 1.0
    negative_marks: float = 0.0
    tags: list[str] | None = None
    difficulty: str | None = None   # easy | medium | hard

    @field_validator('correct_answer')
    @classmethod
    def validate_correct_answer(cls, v, info):
        item_type = info.data.get('type')
        if item_type in (ItemType.MCQ_SINGLE, ItemType.MCQ_MULTI, ItemType.TRUE_FALSE, ItemType.NUMERIC):
            if not v:
                raise ValueError(f"correct_answer is required for type {item_type}")
        return v

    @field_validator('options')
    @classmethod
    def validate_options(cls, v, info):
        item_type = info.data.get('type')
        if item_type in (ItemType.MCQ_SINGLE, ItemType.MCQ_MULTI):
            if not v or len(v) < 2:
                raise ValueError("MCQ questions require at least 2 options")
        return v


class ItemUpdate(BaseModel):
    stem: str | None = None
    options: list[str] | None = None
    correct_answer: list[str] | None = None
    explanation: str | None = None
    marks: float | None = None
    negative_marks: float | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    status: ItemStatus | None = None


class ItemRead(BaseModel):
    id: UUID
    org_id: UUID
    subject_id: UUID
    created_by: UUID
    stem: str
    type: str
    options: list | None
    correct_answer: list | None
    explanation: str | None
    marks: float
    negative_marks: float
    tags: list | None
    difficulty: str | None
    status: str
    version: int
    source: str | None
    created_at: datetime
    updated_at: datetime


# ============================================================
# BULK UPLOAD SCHEMAS
# ============================================================

class BulkUploadResult(BaseModel):
    total_rows: int
    successful_rows: int
    failed_rows: int
    errors: list[dict]          # [{row: 3, error: "missing stem"}]
    upload_id: UUID             # reference to BulkUploadLog


class BulkUploadLogRead(BaseModel):
    id: UUID
    subject_id: UUID
    uploaded_by: UUID
    filename: str
    total_rows: int
    successful_rows: int
    failed_rows: int
    errors: list | None
    created_at: datetime


# ============================================================
# CURRENT USER (shared with auth service via JWT)
# ============================================================

class CurrentUser(BaseModel):
    id: UUID
    org_id: UUID
    role: str
    email: str | None = None
    verified: bool = False

# ============================================================
# USER INFORMATION (from auth service via internal httpx)
# ============================================================

class UserSummary(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    email: str | None
    role: str


class SubjectSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    status: str


class SubjectAssignmentEnriched(BaseModel):
    id: UUID
    subject: SubjectSummary          # ← full subject object instead of just subject_id
    assigned_to: UserSummary | None
    assigned_by: UserSummary | None
    created_at: datetime