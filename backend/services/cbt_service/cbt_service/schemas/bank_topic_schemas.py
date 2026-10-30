from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from cbt_service.database.models.enums.item_subject_enums import (
    BankKind, BankStatus, ItemType,
)


def _clean_name(value: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise ValueError("Name cannot be empty.")
    return name


# ------------------------------------------------------------------ banks

class BankCreate(BaseModel):
    name: str = Field(max_length=120)
    kind: BankKind = BankKind.LEVEL
    year: int | None = Field(default=None, ge=1900, le=2100)
    description: str | None = None
    position: int = 0

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)


class BankUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    kind: BankKind | None = None
    year: int | None = Field(default=None, ge=1900, le=2100)
    description: str | None = None
    position: int | None = None
    status: BankStatus | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class BankRead(BaseModel):
    id: UUID
    subject_id: UUID
    name: str
    kind: BankKind
    status: BankStatus
    year: int | None
    description: str | None
    position: int
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    item_count: int = 0          # active questions in this bank
    # Staff given this bank (people limited to their subjects only see banks
    # they're given, plus questions in no bank).
    staff_ids: list[UUID] = Field(default_factory=list)


class StaffBanksSet(BaseModel):
    """All the banks in one subject a staff member may use (replaces their list there)."""
    bank_ids: list[UUID] = Field(default_factory=list, max_length=500)


class StaffBanksRead(BaseModel):
    subject_id: UUID
    user_id: UUID
    bank_ids: list[UUID]


class BankStaffSet(BaseModel):
    """The full list of staff who may use a bank (replaces the current list)."""
    user_ids: list[UUID] = Field(default_factory=list, max_length=500)


# ----------------------------------------------------------------- topics

class TopicCreate(BaseModel):
    name: str = Field(max_length=120)
    parent_id: UUID | None = None
    position: int = 0

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    parent_id: UUID | None = None
    position: int | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


class TopicRead(BaseModel):
    id: UUID
    subject_id: UUID
    parent_id: UUID | None
    name: str
    position: int
    created_at: datetime
    updated_at: datetime
    item_count: int = 0          # active questions tagged with this topic


# ---------------------------------------------- exam: add items by bank/topic

class ExamItemsFromBank(BaseModel):
    """Pick questions from the exam's subject by bank / topic and add them."""
    section_id: UUID | None = None
    bank_id: UUID | None = None
    topic_ids: list[UUID] = Field(default_factory=list)   # match ANY of these
    period_id: UUID | None = None                          # a session also matches its terms
    difficulty: str | None = None          # a difficulty level key
    item_type: ItemType | None = None
    count: int | None = Field(default=None, ge=1, le=500)  # None = all matching
    random: bool = True                                    # with count: random draw


class ExamItemsFromBankResult(BaseModel):
    matched: int            # questions that fit the filters and weren't already in the exam
    added: int
    item_ids: list[UUID]
