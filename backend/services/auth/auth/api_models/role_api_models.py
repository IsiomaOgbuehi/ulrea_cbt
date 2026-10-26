from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _clean(value: str) -> str:
    text = " ".join(value.split())
    if not text:
        raise ValueError("Cannot be empty.")
    return text


class PermissionInfoRead(BaseModel):
    key: str
    group: str
    label: str
    description: str
    scoped: bool                 # supports "assigned" / "own" reach
    implies: list[str]
    candidate_only: bool         # only for candidates; never in staff roles


class RoleRead(BaseModel):
    key: str                     # "teacher" for built-in roles; a slug for custom ones
    id: UUID | None              # custom roles (and edited built-in roles) have a row
    name: str
    description: str | None
    base_role: str
    is_system: bool              # built-in (admin, teacher, supervisor, staff)
    is_customised: bool          # built-in role whose permissions were edited
    editable: bool               # false for the owner
    permissions: dict[str, str]  # as chosen (implied ones not added)
    member_count: int
    updated_at: datetime | None = None


class RoleCreate(BaseModel):
    name: str = Field(max_length=80)
    description: str | None = Field(default=None, max_length=300)
    permissions: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean(v)


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)          # custom roles only
    description: str | None = Field(default=None, max_length=300)
    permissions: dict[str, str] | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean(v)


class AssignRoleRequest(BaseModel):
    role: str                    # a role key from GET /roles


class MyPermissionsRead(BaseModel):
    role: str                    # base role on the membership
    role_key: str
    role_name: str
    permissions: dict[str, str]  # effective, implied ones included
