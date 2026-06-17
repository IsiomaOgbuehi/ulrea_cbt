from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


class CohortCreate(BaseModel):
    name: str
    description: str | None = None
    academic_year: str | None = None    # optional — schools use this, orgs may not
    start_date: datetime | None = None
    end_date: datetime | None = None


class CohortUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    academic_year: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


class CohortRead(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    description: str | None
    status: str
    academic_year: str | None
    start_date: datetime | None
    end_date: datetime | None
    graduated_at: datetime | None
    created_by: UUID
    created_at: datetime
    member_count: int = 0           # computed
    member_count: int = 0       # computed, not stored


class GraduateCohortRequest(BaseModel):
    reason: str | None = None       # optional note e.g. "End of 2024/2025 session"

class AddMembersRequest(BaseModel):
    student_ids: list[UUID]


class RemoveMemberRequest(BaseModel):
    student_id: UUID


class CohortMemberRead(BaseModel):
    id: UUID
    cohort_id: UUID
    student_id: UUID
    added_by: UUID
    created_at: datetime
    # enriched fields
    firstname: str | None = None
    lastname: str | None = None
    email: str | None = None
    access_code: str | None = None
    institution_id: str | None = None