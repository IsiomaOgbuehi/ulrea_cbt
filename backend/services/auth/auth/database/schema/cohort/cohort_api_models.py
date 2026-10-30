from pydantic import BaseModel, Field, model_validator
from typing import Generic, Literal, TypeVar
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
    """
    Graduate every current student. The cohort then stays open for the next
    set of students ("keep_open", the default) or is archived ("archive").
    """
    note: str | None = Field(default=None, max_length=300)   # e.g. "2024/2025 session"
    reason: str | None = None       # older name for `note`
    then: Literal["keep_open", "archive"] = "keep_open"


class GraduateCohortResult(BaseModel):
    cohort: "CohortRead"
    graduated: int                  # students graduated by this call

class AddMembersRequest(BaseModel):
    student_ids: list[UUID]


class SkippedMember(BaseModel):
    student_id: UUID
    reason: str  # "not_in_org" | "not_a_student" | "inactive:<status>"


class AddMembersResponse(BaseModel):
    added: int
    already_members: int = 0
    not_found: int = 0
    skipped: list[SkippedMember]
    cohort_id: UUID

class RemoveMemberRequest(BaseModel):
    student_id: UUID


T = TypeVar("T")

class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


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
    # active | graduated | left, and when / why it ended (graduated or left)
    status: str = "active"
    ended_at: datetime | None = None
    end_note: str | None = None




class AssignTeacherRequest(BaseModel):
    teacher_id: UUID
 
 
class TeacherCohortAssignmentRead(BaseModel):
    id: UUID
    teacher_id: UUID
    teacher_name: str | None = None   # joined from UserModel for display
    cohort_id: UUID
    assigned_by: UUID
    assigned_at: datetime
 
 
class MyCohortRead(BaseModel):
    """Used to populate the cohort picker when a teacher creates an exam."""
    id: UUID
    name: str
    status: str
    student_count: int

MemberAction = Literal["graduate", "move", "remove"]


class MemberActionRequest(BaseModel):
    """Graduate, move or remove chosen students. The cohort itself carries on."""
    action: MemberAction
    student_ids: list[UUID] = Field(min_length=1, max_length=5000)
    target_cohort_id: UUID | None = None     # required for "move"
    note: str | None = Field(default=None, max_length=300)   # e.g. "2024/2025 session"

    @model_validator(mode="after")
    def _target(self) -> "MemberActionRequest":
        if self.action == "move" and self.target_cohort_id is None:
            raise ValueError("Choose the cohort to move the students to.")
        return self


class MemberActionResult(BaseModel):
    action: MemberAction
    done: int                       # students graduated / moved / removed
    skipped: list[SkippedMember]    # e.g. reason "not_an_active_member"
    target_cohort_id: UUID | None = None
    added_to_target: int = 0        # for "move": new members of the target (others were already in it)


class StudentGraduation(BaseModel):
    cohort_id: UUID
    cohort_name: str
    graduated_at: datetime | None


class CohortDeletableRead(BaseModel):
    """Whether a cohort can be deleted for good: only if it never had students or exams."""
    deletable: bool
    reason: str | None = None
