from enum import Enum


class ItemType(str, Enum):
    MCQ_SINGLE = "mcq_single"       # one correct answer
    MCQ_MULTI = "mcq_multi"         # multiple correct answers
    SHORT_ANSWER = "short_answer"   # one line; auto-marked against accepted answers, else marked by a person
    NUMERIC = "numeric"             # number answer, auto-marked
    TRUE_FALSE = "true_false"
    ESSAY = "essay"                 # long rich-text answer; always marked by a person (marking guide)


# Answers someone has to mark (unless a short answer matches an accepted answer).
WRITTEN_TYPES: frozenset[ItemType] = frozenset({ItemType.SHORT_ANSWER, ItemType.ESSAY})


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
    TEST = "test"
    CAT1 = "cat1"
    CAT2 = "cat2"
    CAT3 = "cat3"
    EXAM = "exam"


class ItemSource(str, Enum):
    MANUAL = "manual"
    EXCEL_UPLOAD = "excel_upload"
    AI_GENERATED = "ai_generated"
    IMPORTED = "imported"

class BankKind(str, Enum):
    """What a question bank represents within a subject."""
    LEVEL = "level"             # a class or level, e.g. JSS1, SS2, Year 10
    PAST_PAPER = "past_paper"   # e.g. WASSCE 2008 (June)
    CUSTOM = "custom"           # anything else: a CAT, a teacher's own set


class BankStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class PeriodStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class DifficultyStatus(str, Enum):
    ACTIVE = "active"        # offered for new questions
    ARCHIVED = "archived"    # kept on existing questions, not offered
