"""
The marking queue: for one exam, every written question (short answer /
essay) with how many answers wait for a person, and the answers themselves
question by question, so a marker works through one question for every
student (faster and fairer than script by script). Scripts (one per
submitted attempt) can also be marked student by student.

Marking an answer goes through AttemptService.manual_review, which caps the
marks and re-scores the attempt.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from cbt_service.core.permissions import P
from cbt_service.database.models.attempt import AttemptModel, ResponseModel
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.database.models.exam import ExamItem, ExamModel
from cbt_service.database.models.item import ItemModel
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.item_subject_schemas import PaginatedResponse
from cbt_service.schemas.marking_schemas import (
    MarkingAnswer, MarkingFilter, MarkingOverview, MarkingQuestion, MarkingQuestionInfo,
    MarkingScript, MarkingScriptDetail, MarkingScriptEntry, MarkingScriptPage, ScriptCounts, ScriptFilter,
)
from cbt_service.services.exam_service import ExamService
from cbt_service.services.marking.scoring import WRITTEN_TYPES, answer_text, matches_accepted

# Attempts whose answers can be marked.
CLOSED_STATUSES: tuple[AttemptStatus, ...] = (
    AttemptStatus.SUBMITTED, AttemptStatus.SCORED, AttemptStatus.TERMINATED,
)


def _type(item: ItemModel) -> str:
    return str(getattr(item.item_type, "value", item.item_type))


def _word_count(answer: list[str] | None) -> int:
    return sum(len(answer_text(str(a)).split()) for a in (answer or []))


def _accepted(item: ItemModel) -> list[str] | None:
    return item.correct_answers if _type(item) == "short_answer" else None


def _question_info(exam_item: ExamItem, item: ItemModel) -> MarkingQuestionInfo:
    return MarkingQuestionInfo(
        exam_item_id=exam_item.id, item_id=item.id, item_type=_type(item),
        question_text=item.question_text, marks=exam_item.marks,
        marking_guide=item.marking_guide, accepted_answers=_accepted(item),
        word_limit=item.word_limit,
    )


def _to_answer(response: ResponseModel, attempt: AttemptModel, accepted: list[str] | None) -> MarkingAnswer:
    return MarkingAnswer(
        response_id=response.id, attempt_id=attempt.id, student_id=attempt.student_id,
        answer=response.answer, word_count=_word_count(response.answer),
        marks_awarded=response.marks_awarded, is_correct=response.is_correct,
        review_notes=response.review_notes, reviewed_by=response.reviewed_by,
        reviewed_at=response.reviewed_at,
        auto_marked=response.marks_awarded is not None and response.reviewed_at is None,
        matches_accepted=matches_accepted(accepted, response.answer) if accepted else None,
        submitted_at=attempt.submitted_at,
    )


def _to_script(attempt: AttemptModel, counts: tuple[int, int, int]) -> MarkingScript:
    written, pending, marked = counts
    return MarkingScript(
        attempt_id=attempt.id, student_id=attempt.student_id,
        attempt_number=attempt.attempt_number,
        status=str(getattr(attempt.status, "value", attempt.status)),
        submitted_at=attempt.submitted_at, final_score=attempt.final_score,
        percentage=attempt.percentage, passed=attempt.passed,
        written=written, pending=pending, marked=marked,
        auto_marked=written - pending - marked,
    )


class MarkingService:

    @staticmethod
    def _exam(session: Session, exam_id: UUID, current_user: CurrentUser) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.RESULTS_MARK)
        return exam

    @staticmethod
    def _written_items(session: Session, exam_id: UUID) -> list[tuple[ExamItem, ItemModel]]:
        rows = session.exec(
            select(ExamItem, ItemModel)
            .join(ItemModel, col(ItemModel.id) == col(ExamItem.item_id))
            .where(ExamItem.exam_id == exam_id)
            .order_by(col(ExamItem.order))
        ).all()
        return [(ei, item) for ei, item in rows if _type(item) in WRITTEN_TYPES]

    @staticmethod
    def overview(session: Session, exam_id: UUID, current_user: CurrentUser) -> MarkingOverview:
        exam = MarkingService._exam(session, exam_id, current_user)
        written = MarkingService._written_items(session, exam.id)
        # Answers are matched by question (item) id: one entry per question per exam.
        item_ids: list[UUID] = [ei.item_id for ei, _ in written]

        submitted: int = int(session.exec(
            select(func.count()).select_from(AttemptModel).where(
                AttemptModel.exam_id == exam.id, col(AttemptModel.status).in_(CLOSED_STATUSES),
            )
        ).one())

        # One grouped query: per question, answered / pending / marked by a person.
        counts: dict[UUID, tuple[int, int, int]] = {}
        waiting_attempts: int = 0
        if item_ids:
            base = (
                select(ResponseModel)
                .join(AttemptModel, col(AttemptModel.id) == col(ResponseModel.attempt_id))
                .where(
                    AttemptModel.exam_id == exam.id,
                    col(AttemptModel.status).in_(CLOSED_STATUSES),
                    col(ResponseModel.item_id).in_(item_ids),
                )
            ).subquery()
            for item_id, answered, pending, marked in session.exec(
                select(
                    base.c.item_id,
                    func.count(),
                    func.count().filter(base.c.marks_awarded.is_(None)),
                    func.count().filter(base.c.reviewed_at.is_not(None)),
                ).group_by(base.c.item_id)
            ).all():
                counts[item_id] = (int(answered), int(pending), int(marked))
            waiting_attempts = int(session.exec(
                select(func.count(func.distinct(base.c.attempt_id))).where(base.c.marks_awarded.is_(None))
            ).one())

        questions: list[MarkingQuestion] = []
        for ei, item in written:
            answered, pending, marked = counts.get(ei.item_id, (0, 0, 0))
            questions.append(MarkingQuestion(
                **_question_info(ei, item).model_dump(),
                answered=answered, pending=pending, marked=marked,
                auto_marked=answered - pending - marked,
            ))
        return MarkingOverview(
            exam_id=exam.id, title=exam.title, total_marks=exam.total_marks,
            auto_mark_short_answers=bool(exam.auto_mark_short_answers),
            submitted_attempts=submitted, attempts_waiting=waiting_attempts,
            pending=sum(q.pending for q in questions), marked=sum(q.marked for q in questions),
            questions=questions,
        )

    @staticmethod
    def answers(
        session: Session,
        exam_id: UUID,
        exam_item_id: UUID,
        current_user: CurrentUser,
        status: MarkingFilter = MarkingFilter.PENDING,
        page: int = 1,
        page_size: int = 50,
    ) -> PaginatedResponse[MarkingAnswer]:
        exam = MarkingService._exam(session, exam_id, current_user)
        exam_item = session.get(ExamItem, exam_item_id)
        if exam_item is None or exam_item.exam_id != exam.id:
            raise HTTPException(status_code=404, detail="Question not found in this exam.")
        item = session.get(ItemModel, exam_item.item_id)
        accepted: list[str] | None = _accepted(item) if item is not None else None

        conditions = [
            AttemptModel.exam_id == exam.id,
            col(AttemptModel.status).in_(CLOSED_STATUSES),
            ResponseModel.item_id == exam_item.item_id,
        ]
        if status == MarkingFilter.PENDING:
            conditions.append(col(ResponseModel.marks_awarded).is_(None))
        elif status == MarkingFilter.MARKED:
            conditions.append(col(ResponseModel.reviewed_at).is_not(None))
        elif status == MarkingFilter.AUTO:
            conditions += [col(ResponseModel.marks_awarded).is_not(None), col(ResponseModel.reviewed_at).is_(None)]

        joined = (
            select(ResponseModel, AttemptModel)
            .join(AttemptModel, col(AttemptModel.id) == col(ResponseModel.attempt_id))
            .where(*conditions)
        )
        total: int = int(session.exec(select(func.count()).select_from(joined.subquery())).one())
        rows = session.exec(
            joined
            # Waiting ones first, then in the order students submitted.
            .order_by(col(ResponseModel.marks_awarded).is_not(None), col(AttemptModel.submitted_at), col(ResponseModel.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        answers = [_to_answer(r, a, accepted) for r, a in rows]
        return PaginatedResponse[MarkingAnswer](
            items=answers, total=total, page=page, page_size=page_size,
            total_pages=max(1, -(-total // page_size)),
        )

    # ------------------------------------------------------------ by student

    @staticmethod
    def _script_counts(
        session: Session, exam_id: UUID, item_ids: list[UUID], attempt_ids: list[UUID] | None = None,
    ) -> dict[UUID, tuple[int, int, int]]:
        """Per attempt: written answers, waiting for a person, marked by a person."""
        if not item_ids:
            return {}
        conditions = [
            AttemptModel.exam_id == exam_id,
            col(AttemptModel.status).in_(CLOSED_STATUSES),
            col(ResponseModel.item_id).in_(item_ids),
        ]
        if attempt_ids is not None:
            conditions.append(col(AttemptModel.id).in_(attempt_ids))
        base = (
            select(ResponseModel)
            .join(AttemptModel, col(AttemptModel.id) == col(ResponseModel.attempt_id))
            .where(*conditions)
        ).subquery()
        rows = session.exec(
            select(
                base.c.attempt_id,
                func.count(),
                func.count().filter(base.c.marks_awarded.is_(None)),
                func.count().filter(base.c.reviewed_at.is_not(None)),
            ).group_by(base.c.attempt_id)
        ).all()
        return {attempt_id: (int(w), int(p), int(m)) for attempt_id, w, p, m in rows}

    @staticmethod
    def script_list(session: Session, exam_id: UUID, current_user: CurrentUser) -> list[MarkingScript]:
        """
        Every submitted script, numbered in submission order ("Script N"), then
        sorted: those with answers waiting first, then in the order submitted.
        """
        exam = MarkingService._exam(session, exam_id, current_user)
        item_ids: list[UUID] = [ei.item_id for ei, _ in MarkingService._written_items(session, exam.id)]
        attempts = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == exam.id, col(AttemptModel.status).in_(CLOSED_STATUSES),
            )
        ).all()
        counts = MarkingService._script_counts(session, exam.id, item_ids)
        far_future = datetime.max.replace(tzinfo=timezone.utc)

        def submitted(a: AttemptModel) -> tuple[datetime, str]:
            when = a.submitted_at
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return (when or far_future, str(a.id))

        scripts: list[MarkingScript] = []
        for n, attempt in enumerate(sorted(attempts, key=submitted), start=1):
            script = _to_script(attempt, counts.get(attempt.id, (0, 0, 0)))
            script.number = n
            scripts.append(script)
        # Stable sort keeps submission order within each group.
        scripts.sort(key=lambda s: s.pending == 0)
        return scripts

    @staticmethod
    def page_scripts(
        scripts: list[MarkingScript],
        status: ScriptFilter = ScriptFilter.ALL,
        search: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> MarkingScriptPage:
        """
        Filter, search and page an already sorted script list. Search matches the
        student's name or number (fill those in first), or "Script N" / N, so it
        also works when names are hidden.
        """
        counts = ScriptCounts(
            pending=sum(1 for s in scripts if s.pending > 0),
            done=sum(1 for s in scripts if s.pending == 0),
            all=len(scripts),
        )
        if status == ScriptFilter.PENDING:
            scripts = [s for s in scripts if s.pending > 0]
        elif status == ScriptFilter.DONE:
            scripts = [s for s in scripts if s.pending == 0]
        terms: list[str] = (search or "").lower().split()
        if terms:
            def haystack(s: MarkingScript) -> str:
                return " ".join([s.student_name or "", s.student_number or "", f"script {s.number}"]).lower()
            scripts = [s for s in scripts if all(t in haystack(s) for t in terms)]
        total: int = len(scripts)
        start: int = (page - 1) * page_size
        return MarkingScriptPage(
            items=scripts[start:start + page_size], total=total, page=page, page_size=page_size,
            total_pages=max(1, -(-total // page_size)), counts=counts,
        )

    @staticmethod
    def script(
        session: Session, exam_id: UUID, attempt_id: UUID, current_user: CurrentUser,
    ) -> MarkingScriptDetail:
        """One student's written answers in exam order, with unanswered questions included."""
        exam = MarkingService._exam(session, exam_id, current_user)
        attempt = session.get(AttemptModel, attempt_id)
        if attempt is None or attempt.exam_id != exam.id or attempt.org_id != exam.org_id:
            raise HTTPException(status_code=404, detail="Script not found for this exam.")
        if attempt.status not in CLOSED_STATUSES:
            raise HTTPException(status_code=409, detail="This attempt hasn't been submitted yet.")
        written = MarkingService._written_items(session, exam.id)
        item_ids: list[UUID] = [ei.item_id for ei, _ in written]
        responses: dict[UUID, ResponseModel] = {
            r.item_id: r for r in session.exec(
                select(ResponseModel).where(
                    ResponseModel.attempt_id == attempt.id, col(ResponseModel.item_id).in_(item_ids),
                )
            ).all()
        } if item_ids else {}
        entries: list[MarkingScriptEntry] = []
        for ei, item in written:
            response = responses.get(ei.item_id)
            entries.append(MarkingScriptEntry(
                question=_question_info(ei, item),
                answer=_to_answer(response, attempt, _accepted(item)) if response is not None else None,
            ))
        counts = MarkingService._script_counts(session, exam.id, item_ids, [attempt.id])
        summary = _to_script(attempt, counts.get(attempt.id, (0, 0, 0)))
        if attempt.submitted_at is not None:
            # "Script N": how many scripts were submitted up to this one.
            summary.number = int(session.exec(
                select(func.count()).select_from(AttemptModel).where(
                    AttemptModel.exam_id == exam.id,
                    col(AttemptModel.status).in_(CLOSED_STATUSES),
                    col(AttemptModel.submitted_at) <= attempt.submitted_at,
                )
            ).one())
        return MarkingScriptDetail(**summary.model_dump(), total_marks=exam.total_marks, entries=entries)
