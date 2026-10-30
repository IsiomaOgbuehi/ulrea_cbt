from enum import Enum


class MemberStatus(str, Enum):
    """A student's place in a cohort. Only ACTIVE members count, get exams or appear by default."""
    ACTIVE = "active"
    GRADUATED = "graduated"     # finished with this cohort; the cohort carries on
    LEFT = "left"               # removed, or moved to another cohort


class CohortStatus(str, Enum):
    ACTIVE = "active"           # accepting students, can be assigned exams
    GRADUATED = "graduated"     # closed — no new exams, read-only
    ARCHIVED = "archived"       # hidden from normal views