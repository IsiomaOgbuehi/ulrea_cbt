from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB

from .enums.item_subject_enums import ItemDifficulty, ItemSource, ItemStatus, ItemType


json_type = JSON().with_variant(JSONB, "postgresql")

class ItemModel(SQLModel, table=True):
    """
    A single exam question (item).
    Options and correct_answer stored as JSON to support all question types.

    MCQ single:  options=["A","B","C","D"], correct_answer=["A"]
    MCQ multi:   options=["A","B","C","D"], correct_answer=["A","C"]
    True/False:  options=["True","False"],  correct_answer=["True"]
    Short answer: options=null, correct_answer=null (manual review)
    Numeric:     options=null, correct_answer=["42.5"]
    """
    __tablename__ = "items"
    __table_args__ = (
        # Serves item listing/counts: filter subject_id + org_id + status,
        # sort by created_at (Postgres scans it backwards for DESC).
        # Created in migration 2fabab7d6959.
        Index(
            "ix_items_subject_org_status_created",
            "subject_id", "org_id", "status", "created_at",
        ),
    )

    # ----------------------------------------------------------------
    # Identity / Ownership
    # ----------------------------------------------------------------

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    # Optional: which bank (class/level, past paper...) in the subject this question
    # belongs to. NULL = not in a bank (all questions created before banks existed).
    bank_id: UUID | None = Field(default=None, foreign_key="question_banks.id", index=True)
    # Optional: the session/term (or year/cycle) the question was prepared for.
    period_id: UUID | None = Field(default=None, foreign_key="periods.id", index=True)
    created_by: UUID = Field(nullable=False, index=True,)

    # ----------------------------------------------------------------
    # Question Content
    # ----------------------------------------------------------------
    question_text: str                                                    # the question text
    item_type: ItemType = Field(index=True, nullable=False)                                # ItemType enum value
    options: list[dict] | None = Field(default=None, sa_column=Column(json_type))
    correct_answers: list[str] | None = Field(default=None, sa_column=Column(json_type))
    explanation: str | None = None                               # shown after exam

    # ----------------------------------------------------------------
    # Scoring
    # ----------------------------------------------------------------
    marks: float = Field(default=1.0, ge=0,)                           # points for correct answer
    negative_marks: float = Field(default=0.0, ge=0,)                  # penalty for wrong answer

    # ----------------------------------------------------------------
    # Metadata
    # ----------------------------------------------------------------
    tags: list[str] = Field(default_factory=list, sa_column=Column(json_type))  # e.g. ["algebra","chapter-3"]
    difficulty: ItemDifficulty = Field(default=ItemDifficulty.MEDIUM, index=True)                                # easy | medium | hard
    status: ItemStatus = Field(default=ItemStatus.ACTIVE, index=True,)                        # ItemStatus
    version: int = Field(default=1, ge=1)

    # ----------------------------------------------------------------
    # Import / Upload Tracking
    # ----------------------------------------------------------------
    source: ItemSource = Field(default=ItemSource.MANUAL, index=True,)                                    # "manual" | "excel_upload"
    bulk_upload_id: UUID | None = Field(default=None, index=True,)                           # groups items from same upload

    # ----------------------------------------------------------------
    # Timestamps
    # ----------------------------------------------------------------
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=True),)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=True),)


class BulkUploadLog(SQLModel, table=True):
    """
    Tracks each bulk upload attempt for auditing and retry purposes.
    """
    __tablename__ = "bulk_upload_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    uploaded_by: UUID = Field(nullable=False)
    filename: str
    total_rows: int = Field(default=0)
    successful_rows: int = Field(default=0)
    failed_rows: int = Field(default=0)
    errors: list | None = Field(default=None, sa_column=Column(json_type))  # [{row: 3, error: "..."}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), nullable=True),)
