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

class ItemDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ItemSource(str, Enum):
    MANUAL = "manual"
    EXCEL_UPLOAD = "excel_upload"
    AI_GENERATED = "ai_generated"
    IMPORTED = "imported"