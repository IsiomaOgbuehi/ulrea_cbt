from enum import Enum


class CohortStatus(str, Enum):
    ACTIVE = "active"           # accepting students, can be assigned exams
    GRADUATED = "graduated"     # closed — no new exams, read-only
    ARCHIVED = "archived"       # hidden from normal views