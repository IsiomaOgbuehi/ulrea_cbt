"""
Question difficulty levels, set up per organisation.

Every organisation starts with the built-in levels (Easy, Medium, Hard, Test,
CAT 1-3, Exam). Admins can rename, reorder, colour, archive or add their own.
A question stores the level's `key`, which never changes, so renaming a level
never touches questions.

Rows are created for an organisation the first time its levels are read.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, UniqueConstraint
from sqlmodel import Field, SQLModel

from .enums.item_subject_enums import DifficultyStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


# key, name, colour — in the order they're offered. Medium is the default.
BUILT_IN_LEVELS: list[tuple[str, str, str]] = [
    ("easy", "Easy", "emerald"),
    ("medium", "Medium", "amber"),
    ("hard", "Hard", "rose"),
    ("test", "Test", "sky"),
    ("cat1", "CAT 1", "violet"),
    ("cat2", "CAT 2", "violet"),
    ("cat3", "CAT 3", "violet"),
    ("exam", "Exam", "slate"),
]
DEFAULT_LEVEL_KEY = "medium"
LEVEL_COLOURS: frozenset[str] = frozenset(
    {"slate", "emerald", "lime", "amber", "orange", "rose", "pink", "violet", "sky", "teal"}
)


class DifficultyLevel(SQLModel, table=True):
    __tablename__ = "difficulty_levels"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_difficulty_levels_org_key"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    org_id: UUID = Field(index=True, nullable=False)
    # What questions store; lower-case slug, fixed once created.
    key: str = Field(sa_column=Column(String(40), nullable=False))
    name: str = Field(sa_column=Column(String(60), nullable=False))
    description: str | None = Field(default=None, sa_column=Column(String(300), nullable=True))
    colour: str = Field(default="slate", sa_column=Column(String(20), nullable=False, server_default="slate"))
    position: int = Field(default=0)                 # display order, easiest first
    is_default: bool = Field(default=False)          # picked for new questions
    is_built_in: bool = Field(default=False)
    status: str = Field(
        default=DifficultyStatus.ACTIVE.value,
        sa_column=Column(String(20), nullable=False, server_default=DifficultyStatus.ACTIVE.value),
    )
    created_by: UUID | None = Field(default=None)    # None for the built-in ones
    created_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_now, sa_column=Column(DateTime(timezone=True), nullable=False))
