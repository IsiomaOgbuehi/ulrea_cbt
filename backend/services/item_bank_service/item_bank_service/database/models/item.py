from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON


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

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    created_by: UUID = Field(nullable=False)

    # Question content
    stem: str                                                    # the question text
    type: str = Field(index=True)                                # ItemType enum value
    options: list | None = Field(default=None, sa_column=Column(JSON))
    correct_answer: list | None = Field(default=None, sa_column=Column(JSON))
    explanation: str | None = None                               # shown after exam
    marks: float = Field(default=1.0)                           # points for correct answer
    negative_marks: float = Field(default=0.0)                  # penalty for wrong answer

    # Metadata
    tags: list | None = Field(default=None, sa_column=Column(JSON))  # e.g. ["algebra","chapter-3"]
    difficulty: str | None = None                                # easy | medium | hard
    status: str = Field(default="active")                        # ItemStatus
    version: int = Field(default=1)

    # Bulk upload tracking
    source: str | None = None                                    # "manual" | "excel_upload"
    bulk_upload_id: UUID | None = None                           # groups items from same upload

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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
    errors: list | None = Field(default=None, sa_column=Column(JSON))  # [{row: 3, error: "..."}]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
