from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from cbt_service.database.models.enums.item_subject_enums import PeriodStatus


def _clean(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise ValueError("Cannot be empty.")
    return text


# ---------------------------------------------------------------- periods

class PeriodCreate(BaseModel):
    name: str = Field(max_length=80)
    parent_id: UUID | None = None          # set to add a term / cycle inside a session / year
    starts_on: date | None = None
    ends_on: date | None = None
    position: int = 0

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v)

    @model_validator(mode="after")
    def _dates(self) -> Self:
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("The end date must be on or after the start date.")
        return self


class PeriodUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    starts_on: date | None = None
    ends_on: date | None = None
    position: int | None = None
    status: PeriodStatus | None = None     # archive / restore

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)


class PeriodRead(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    status: PeriodStatus
    starts_on: date | None
    ends_on: date | None
    position: int
    created_at: datetime
    updated_at: datetime
    item_count: int = 0          # active questions tagged with exactly this period
    exam_count: int = 0          # exams tagged with exactly this period
    is_current: bool = False


# --------------------------------------------------------- org settings

class OrgSettingsRead(BaseModel):
    period_parent_label: str
    period_child_label: str
    current_period_id: UUID | None
    # True until an admin saves settings once; the app may then suggest
    # labels that suit the organisation type.
    is_default: bool
    updated_at: datetime | None


class OrgSettingsUpdate(BaseModel):
    period_parent_label: str | None = Field(default=None, max_length=40)
    period_child_label: str | None = Field(default=None, max_length=40)
    current_period_id: UUID | None = None   # send null to clear

    @field_validator("period_parent_label", "period_child_label")
    @classmethod
    def _label(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)
