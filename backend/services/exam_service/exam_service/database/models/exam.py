from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlmodel import SQLModel, Field
from sqlalchemy import Column, JSON


class ExamModel(SQLModel, table=True):
    """
    An exam created by a teacher from items in an assigned subject.
    Requires admin approval before becoming active.
    """
    __tablename__ = "exams"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(index=True, nullable=False)        # from item bank service
    created_by: UUID = Field(nullable=False)                    # teacher user id
    approved_by: UUID | None = Field(default=None)              # admin who approved
    rejected_by: UUID | None = Field(default=None)

    title: str
    description: str | None = None
    instructions: str | None = None
    duration_minutes: int = Field(default=60)
    pass_mark: float | None = None                              # optional pass/fail threshold
    total_marks: float = Field(default=0.0)                     # computed from items

    status: str = Field(default="draft", index=True)           # ExamStatus
    rejection_reason: str | None = None

    # Scheduling
    start_time: datetime | None = None
    end_time: datetime | None = None

    # Settings
    shuffle_questions: bool = Field(default=False)
    shuffle_options: bool = Field(default=False)
    show_result_immediately: bool = Field(default=True)
    allow_review: bool = Field(default=True)                    # can student review answers
    max_attempts: int = Field(default=1)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExamSection(SQLModel, table=True):
    """
    Optional sections within an exam (e.g. Section A: MCQ, Section B: Theory).
    If no sections, all items are in one flat list.
    """
    __tablename__ = "exam_sections"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    exam_id: UUID = Field(foreign_key="exams.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    title: str
    instructions: str | None = None
    time_limit_minutes: int | None = None                       # per-section timer
    order: int = Field(default=0)


class ExamItem(SQLModel, table=True):
    """
    Junction table linking items from item bank to an exam/section.
    Stores a snapshot of marks to guard against item edits after exam starts.
    """
    __tablename__ = "exam_items"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    exam_id: UUID = Field(foreign_key="exams.id", index=True, nullable=False)
    section_id: UUID | None = Field(default=None, foreign_key="exam_sections.id")
    item_id: UUID = Field(index=True, nullable=False)           # from item bank service
    org_id: UUID = Field(index=True, nullable=False)
    order: int = Field(default=0)
    marks: float = Field(default=1.0)                           # snapshot at time of addition
    negative_marks: float = Field(default=0.0)


class ExamAssignment(SQLModel, table=True):
    """
    Assigns a student to an exam. Created by teacher/admin.
    """
    __tablename__ = "exam_assignments"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    exam_id: UUID = Field(foreign_key="exams.id", index=True, nullable=False)
    student_id: UUID = Field(index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    assigned_by: UUID = Field(nullable=False)
    status: str = Field(default="assigned")                     # AssignmentStatus
    scheduled_at: datetime | None = None                        # override global exam time
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExamAuditLog(SQLModel, table=True):
    """
    Append-only log of all significant exam lifecycle events.
    """
    __tablename__ = "exam_audit_logs"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    exam_id: UUID = Field(foreign_key="exams.id", index=True, nullable=False)
    org_id: UUID = Field(index=True, nullable=False)
    actor_id: UUID = Field(nullable=False)
    action: str                                                 # e.g. "submitted_for_approval"
    extra_data: dict | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
