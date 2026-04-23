from enum import Enum


class ItemType(str, Enum):
    MCQ_SINGLE = "mcq_single"       # one correct answer
    MCQ_MULTI = "mcq_multi"         # multiple correct answers
    SHORT_ANSWER = "short_answer"   # free text, manual review
    NUMERIC = "numeric"             # number answer, auto-marked
    TRUE_FALSE = "true_false"


class ItemStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SubjectStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"
    SUPERVISOR = "supervisor"
