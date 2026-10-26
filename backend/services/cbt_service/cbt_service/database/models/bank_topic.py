"""
Banks and topics organise questions inside a subject.

- QuestionBank: where a question belongs, e.g. a class/level (SS2), a past
  paper (WASSCE 2008) or a custom set. A question is in at most one bank.
- Topic: what a question tests, from the subject's syllabus. Topics are shared
  by every bank in the subject and may be nested one level or more via
  parent_id (e.g. Algebra > Quadratic equations). A question can have several.

Both are optional on items and exams, so everything created before banks and
topics existed keeps working unchanged.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums.item_subject_enums import BankKind, BankStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QuestionBank(SQLModel, table=True):
    __tablename__ = "question_banks"
    __table_args__ = (
        UniqueConstraint("subject_id", "name", name="uq_question_banks_subject_name"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    name: str = Field(nullable=False)
    # Stored as plain strings (validated by the schemas) to avoid Postgres enum
    # migrations whenever a new kind is added.
    kind: str = Field(
        default=BankKind.LEVEL.value,
        sa_column=Column(String(20), nullable=False, server_default=BankKind.LEVEL.value),
    )
    status: str = Field(
        default=BankStatus.ACTIVE.value,
        sa_column=Column(String(20), nullable=False, server_default=BankStatus.ACTIVE.value),
    )
    year: int | None = None                  # mainly for past papers
    description: str | None = None
    position: int = Field(default=0)         # display order within the subject
    created_by: UUID = Field(nullable=False)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class Topic(SQLModel, table=True):
    __tablename__ = "topics"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    subject_id: UUID = Field(foreign_key="subjects.id", index=True, nullable=False)
    parent_id: UUID | None = Field(default=None, foreign_key="topics.id", index=True)
    name: str = Field(nullable=False)
    position: int = Field(default=0)         # display order among siblings
    created_by: UUID = Field(nullable=False)
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))


class ItemTopic(SQLModel, table=True):
    """Which topics a question tests (many-to-many)."""
    __tablename__ = "item_topics"

    item_id: UUID = Field(foreign_key="items.id", primary_key=True)
    topic_id: UUID = Field(foreign_key="topics.id", primary_key=True, index=True)


class ExamTopic(SQLModel, table=True):
    """Topics an exam is scoped to (optional; empty = whole subject)."""
    __tablename__ = "exam_topics"

    exam_id: UUID = Field(foreign_key="exams.id", primary_key=True)
    topic_id: UUID = Field(foreign_key="topics.id", primary_key=True, index=True)
