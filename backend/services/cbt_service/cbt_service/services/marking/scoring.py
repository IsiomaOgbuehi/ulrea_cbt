"""
Scoring one answer. Pure functions (no database), shared by submission,
re-marking and tests.

- Objective types (MCQ, true/false, numeric): exact match against the key.
- Short answer: marked by a person, unless the exam auto-marks short
  answers; then matched against the accepted answers, ignoring case, extra
  spaces, trailing punctuation and formatting (no accepted answers -> a
  person marks it anyway).
- Essay: always marked by a person.
- A blank written answer scores 0 straight away: there's nothing to mark.
"""
import html
import re
from dataclasses import dataclass

OBJECTIVE_TYPES: frozenset[str] = frozenset({"mcq_single", "mcq_multi", "true_false", "numeric"})
WRITTEN_TYPES: frozenset[str] = frozenset({"short_answer", "essay"})

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_TRAILING = re.compile(r"[\s.,;:!?]+$")
_LEADING = re.compile(r"^[\s\"'“‘]+|[\"'”’]+$")


def answer_text(raw: str) -> str:
    """Plain text of an answer that may be rich text (HTML from the editor)."""
    text = _TAG.sub(" ", raw.replace("<br>", "\n").replace("<br />", "\n"))
    return html.unescape(text).replace("\xa0", " ")


def normalise_answer(raw: str) -> str:
    """For matching short answers: 'The  Nile.' == 'the nile'."""
    text = _SPACE.sub(" ", answer_text(raw)).strip().casefold()
    text = _TRAILING.sub("", text)
    return _LEADING.sub("", text).strip()


def clean_accepted_answers(answers: list[str] | None) -> list[str] | None:
    """Trimmed, de-duplicated (by normalised form), empties dropped; None when nothing's left."""
    if not answers:
        return None
    seen: set[str] = set()
    cleaned: list[str] = []
    for a in answers:
        text = " ".join(str(a).split())
        key = normalise_answer(text)
        if key and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned or None


def matches_accepted(accepted: list[str] | None, answer: list[str] | None) -> bool:
    """Does a short answer match one of the accepted answers (case, spacing, full stops ignored)?"""
    if not accepted or is_blank(answer):
        return False
    given = normalise_answer(" ".join(str(a) for a in (answer or [])))
    return given in {normalise_answer(str(a)) for a in accepted}


def is_blank(answer: list[str] | None) -> bool:
    return not answer or not any(answer_text(str(a)).strip() for a in answer)


@dataclass(frozen=True)
class Score:
    is_correct: bool | None
    marks_awarded: float | None      # None = waiting for a person to mark it

    @property
    def needs_marking(self) -> bool:
        return self.marks_awarded is None


def score_answer(
    item_type: str,
    correct_answers: list[str] | None,
    answer: list[str] | None,
    marks: float,
    negative_marks: float = 0.0,
    auto_mark_short_answers: bool = False,
) -> Score:
    if item_type in OBJECTIVE_TYPES:
        student = sorted(str(a).strip().lower() for a in (answer or []))
        key = sorted(str(a).strip().lower() for a in (correct_answers or []))
        if student and student == key:
            return Score(True, marks)
        if student:
            return Score(False, -negative_marks)
        return Score(False, 0.0)

    if item_type in WRITTEN_TYPES:
        if is_blank(answer):
            return Score(False, 0.0)
        if item_type == "short_answer" and auto_mark_short_answers and correct_answers:
            if matches_accepted(correct_answers, answer):
                return Score(True, marks)
            return Score(False, -negative_marks)
        return Score(None, None)

    return Score(None, None)


def verdict(marks_awarded: float, max_marks: float) -> bool | None:
    """is_correct for a marked answer: full marks True, zero False, partial None."""
    if max_marks > 0 and marks_awarded >= max_marks:
        return True
    if marks_awarded <= 0:
        return False
    return None


def pass_threshold(pass_mark: float | None) -> float | None:
    """Pass marks are percentages; values <= 1 are fractions (0.5 = 50%)."""
    if pass_mark is None:
        return None
    return pass_mark * 100 if pass_mark <= 1 else pass_mark
