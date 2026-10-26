from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from cbt_service.database.models.difficulty import LEVEL_COLOURS
from cbt_service.database.models.enums.item_subject_enums import DifficultyStatus


def _clean_name(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    if not v:
        raise ValueError("Name can't be empty.")
    return v


def _check_colour(v: str | None) -> str | None:
    if v is not None and v not in LEVEL_COLOURS:
        raise ValueError(f"Colour must be one of: {', '.join(sorted(LEVEL_COLOURS))}.")
    return v


class DifficultyLevelRead(BaseModel):
    id: UUID
    key: str
    name: str
    description: str | None
    colour: str
    position: int
    is_default: bool
    is_built_in: bool
    status: DifficultyStatus
    item_count: int = 0          # questions using it (in this organisation)
    created_at: datetime
    updated_at: datetime


class DifficultyLevelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    colour: str = "slate"
    is_default: bool = False

    _name = field_validator("name")(_clean_name)
    _colour = field_validator("colour")(_check_colour)


class DifficultyLevelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    colour: str | None = None
    is_default: bool | None = None       # only `true` is meaningful: makes this the default
    status: DifficultyStatus | None = None

    _name = field_validator("name")(_clean_name)
    _colour = field_validator("colour")(_check_colour)


class DifficultyReorder(BaseModel):
    """Every level's id, easiest first."""
    ids: list[UUID] = Field(min_length=1)
