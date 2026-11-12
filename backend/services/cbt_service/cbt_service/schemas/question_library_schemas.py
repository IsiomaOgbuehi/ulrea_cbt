"""Exam bodies' shared questions: the question library (organisations copy
questions) and topic tests (students practise by topic)."""
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ---- Browsing an exam body's shared questions ---------------------------------

class PoolTopicRead(BaseModel):
    id: UUID
    name: str
    parent_id: UUID | None
    question_count: int


class PoolBankRead(BaseModel):
    id: UUID
    name: str
    kind: str
    year: int | None
    question_count: int


class PoolSubjectRead(BaseModel):
    id: UUID
    name: str
    question_count: int
    topics: list[PoolTopicRead]
    banks: list[PoolBankRead] = Field(default_factory=list)   # question library: the shared banks


# ---- Question library (organisations) ----------------------------------------

class LibraryBodyRead(BaseModel):
    id: UUID
    name: str
    question_count: int


class LibraryRead(BaseModel):
    allowed: bool                    # the organisation's plan includes the library
    reason: str | None = None
    bodies: list[LibraryBodyRead]


class LibraryOptionRead(BaseModel):
    key: str
    text: str


class LibraryItemRead(BaseModel):
    id: UUID
    subject_name: str
    bank_name: str | None
    question_text: str
    item_type: str
    options: list[LibraryOptionRead] | None
    correct_answers: list[str] | None
    explanation: str | None
    marks: float
    difficulty: str
    topics: list[str]
    already_copied: bool             # already in the subject it would be copied into


class LibraryItemPage(BaseModel):
    items: list[LibraryItemRead]
    total: int
    page: int
    page_size: int
    pages: int


MAX_BANK_COPY: int = 2000


class LibraryCopyRequest(BaseModel):
    """Copy chosen questions (item_ids) or a whole shared bank (source_bank_id)
    into one of the organisation's subjects, or into an exam's subject and
    straight into the exam (exam_id)."""
    exam_body_org_id: UUID
    item_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=200)
    source_bank_id: UUID | None = None
    subject_id: UUID | None = None   # required unless exam_id is given (then: the exam's subject)
    exam_id: UUID | None = None
    topic_id: UUID | None = None
    bank_id: UUID | None = None      # put the copies in this bank of the subject
    new_bank: bool = False           # whole bank: put the copies in a bank of the same name (made if needed)

    @model_validator(mode="after")
    def _one_source(self) -> "LibraryCopyRequest":
        if (self.item_ids is None) == (self.source_bank_id is None):
            raise ValueError("Choose questions or a bank to copy.")
        if self.subject_id is None and self.exam_id is None:
            raise ValueError("Choose the subject (or exam) to copy into.")
        if self.new_bank and (self.source_bank_id is None or self.bank_id is not None):
            raise ValueError("A new bank is only for copying a whole bank.")
        return self


class LibraryCopyResult(BaseModel):
    copied: int
    skipped: int                     # already copied into the subject, or no longer shared
    added_to_exam: int = 0           # with exam_id: questions newly added to the exam
    bank_id: UUID | None = None      # where the copies went


# ---- Topic tests (students) ---------------------------------------------------

MAX_TOPIC_TEST_QUESTIONS: int = 50


class TopicTestRequest(BaseModel):
    subject_id: UUID
    topic_ids: list[UUID] = Field(default_factory=list, max_length=50)   # empty: the whole subject
    count: int = Field(default=20, ge=1, le=MAX_TOPIC_TEST_QUESTIONS)


class TopicTestItemRead(BaseModel):
    id: UUID
    question_text: str
    item_type: str
    options: list[LibraryOptionRead] | None     # no answers
    marks: float


class TopicTestRead(BaseModel):
    subject_name: str
    items: list[TopicTestItemRead]


class TopicTestAnswer(BaseModel):
    item_id: UUID
    answer: list[str] | None = None


class TopicTestScoreRequest(BaseModel):
    answers: list[TopicTestAnswer] = Field(min_length=1, max_length=MAX_TOPIC_TEST_QUESTIONS)


class TopicTestMarkedItem(BaseModel):
    item_id: UUID
    your_answer: list[str] | None
    correct_answers: list[str] | None
    is_correct: bool
    marks_awarded: float
    marks: float
    explanation: str | None


class TopicTestResult(BaseModel):
    score: float
    max_score: float
    percentage: float
    items: list[TopicTestMarkedItem]
    missing: int                     # questions no longer shared (not marked)
