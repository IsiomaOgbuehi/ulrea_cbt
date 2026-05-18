from pydantic import BaseModel, Field, field_validator, model_validator
from uuid import UUID
from datetime import datetime
from typing import Any
from item_bank_service.database.models.enums import ItemDifficulty, ItemSource, ItemType, ItemStatus, SubjectStatus


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
    status: SubjectStatus | None = None
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

class ItemOption(BaseModel):
    key: str
    text: str


class ItemCreate(BaseModel):
    question_text: str
    item_type: ItemType
    options: list[ItemOption] | None = None
    correct_answers: list[str] | None = None
    explanation: str | None = None
    marks: float = Field(default=1.0, ge=0)     # This is the score awarded when the question is answered correctly.
    negative_marks: float = Field(default=0.0, ge=0)     # This is the penalty deducted for a wrong answer.
    tags: list[str] = Field(default_factory=list)   # Used for: filtering, analytics, exam generation, topic grouping... Give me 20 questions tagged "calculus"... tags=["math", "arithmetic", "basic"],
    difficulty: ItemDifficulty = ItemDifficulty.MEDIUM   # easy | medium | hard

    @field_validator("options")
    @classmethod
    def validate_options(cls, v, info):
        item = info.data.get("item_type")

        if item in (ItemType.MCQ_SINGLE, ItemType.MCQ_MULTI):
            if not v or len(v) < 2:
                raise ValueError("MCQ items require at least 2 options.")

            # validate structured objects
            for opt in v:
                if not isinstance(opt, ItemOption):
                    raise ValueError("Each option must be an ItemOption")

                if not opt.key or not opt.text:
                    raise ValueError("Option key and text are required")

            keys = [opt.key for opt in v]
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate option keys not allowed")

            return v

        return v
    
    @field_validator("correct_answers")
    @classmethod
    def validate_correct_answer(cls, v, info):

        item = info.data.get("item_type")

        if item in (
            ItemType.MCQ_SINGLE,
            ItemType.MCQ_MULTI,
            ItemType.TRUE_FALSE,
            ItemType.NUMERIC,
        ):
            if not v:
                raise ValueError(
                    f"correct_answer is required for {item}"
                )

        return v
    
    @model_validator(mode="after")
    def validate_item(self):
        if self.item_type in (
            ItemType.MCQ_SINGLE,
            ItemType.MCQ_MULTI,
        ):
            if not self.options or len(self.options) < 2:
                raise ValueError(
                    "MCQ questions require at least 2 options"
                )

            option_keys = {o.key for o in self.options}

            if not self.correct_answers:
                raise ValueError(
                    "Correct answers required"
                )

            invalid = [
                ans for ans in self.correct_answers
                if ans not in option_keys
            ]

            if invalid:
                raise ValueError(
                    f"Invalid correct answers: {invalid}"
                )

        return self


class ItemUpdate(BaseModel):
    question_text: str | None = None
    options: list[ItemOption] | None = None
    correct_answers: list[str] | None = None
    explanation: str | None = None
    marks: float | None = None
    negative_marks: float | None = None
    tags: list[str] | None = None
    difficulty: ItemDifficulty | None = None
    status: ItemStatus | None = None


class ItemRead(BaseModel):
    id: UUID
    org_id: UUID
    subject_id: UUID
    created_by: UUID
    question_text: str
    item_type: ItemType
    options: list | None
    correct_answers: list | None
    explanation: str | None
    marks: float
    negative_marks: float
    tags: list | None
    difficulty: ItemDifficulty | None
    status: ItemStatus
    version: int
    source: ItemSource | None
    created_at: datetime
    updated_at: datetime


# ============================================================
# BULK UPLOAD SCHEMAS
# ============================================================

class BulkUploadResult(BaseModel):
    total_rows: int
    successful_rows: int
    failed_rows: int
    errors: list[dict]          # [{row: 3, error: "missing question_text"}]
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