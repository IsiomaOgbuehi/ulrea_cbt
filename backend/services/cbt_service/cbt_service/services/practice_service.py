"""
External Exams practice: a subscribed student practising an exam body's
exam. One practice attempt per student per exam, reused on every retake
(answers replaced); progress is kept in PracticeSummary. Practice never mixes
with real exams: it has its own assignment and attempts labelled practice.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import ColumnElement, UnaryExpression, and_
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, delete, func, select

from cbt_service.core.permissions import P
from cbt_service.database.models.attempt import AttemptModel, LateResponseModel, ResponseModel
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.database.models.enums.exam_enum import AssignmentStatus, ExamStatus
from cbt_service.database.models.exam import ExamAssignment, ExamItem, ExamModel, PracticeSummary
from cbt_service.database.models.subject import SubjectModel
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.practice_schemas import PracticeExamRead, PracticeSort, PracticeStatsRead
from cbt_service.services.attempt_service import AttemptService
from cbt_service.services.exam_service import ExamService
from cbt_service.services.item.utils.search import escape_like

OPEN_STATUSES: tuple[ExamStatus, ...] = (ExamStatus.APPROVED, ExamStatus.ACTIVE)


class PracticeService:

    @staticmethod
    def listed_exam(session: Session, exam_id: UUID) -> ExamModel:
        """An exam offered in External Exams, or 404."""
        exam = session.get(ExamModel, exam_id)
        if exam is None or not exam.external_listed or ExamStatus(exam.status) not in OPEN_STATUSES:
            raise HTTPException(status_code=404, detail="Exam not found.")
        return exam

    @staticmethod
    def catalogue(
        session: Session,
        body_id: UUID,
        student_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        sort: PracticeSort = "title",
        search: str = "",
    ) -> tuple[list[PracticeExamRead], int]:
        """One page of an exam body's listed exams with this student's progress, and the total."""
        conds: list[ColumnElement[bool]] = [
            col(ExamModel.org_id) == body_id, col(ExamModel.external_listed).is_(True),
            col(ExamModel.status).in_(OPEN_STATUSES),
        ]
        if search.strip():
            conds.append(col(ExamModel.title).ilike(f"%{escape_like(search.strip())}%", escape="\\"))
        total = int(session.exec(select(func.count()).select_from(ExamModel).where(*conds)).one())
        if total == 0:
            return [], 0

        summary = aliased(PracticeSummary)
        stmt = (
            select(ExamModel, summary, SubjectModel.name)
            .outerjoin(summary, and_(col(summary.exam_id) == ExamModel.id, col(summary.student_id) == student_id))
            .outerjoin(SubjectModel, col(SubjectModel.id) == ExamModel.subject_id)
            .where(*conds)
        )
        title = col(ExamModel.title).asc()
        order: dict[str, list[UnaryExpression[object]]] = {
            "title": [title],
            "newest": [col(ExamModel.created_at).desc()],
            "subject": [col(SubjectModel.name).asc().nulls_last(), title],
            "recently_taken": [col(summary.last_taken_at).desc().nulls_last(), title],
            "best_score": [col(summary.best_percentage).desc().nulls_last(), title],
            "not_taken": [func.coalesce(col(summary.times_taken), 0).asc(), title],
        }
        rows = session.exec(
            stmt.order_by(*order[sort], col(ExamModel.id)).offset((page - 1) * page_size).limit(page_size)
        ).all()
        ids = [e.id for e, _, _ in rows]
        counts: dict[UUID, int] = {
            eid: int(n) for eid, n in session.exec(
                select(ExamItem.exam_id, func.count()).where(col(ExamItem.exam_id).in_(ids)).group_by(col(ExamItem.exam_id))
            ).all()
        }
        attempts: dict[UUID, AttemptModel] = {
            a.exam_id: a for a in session.exec(
                select(AttemptModel).where(
                    AttemptModel.student_id == student_id, col(AttemptModel.exam_id).in_(ids),
                    col(AttemptModel.practice).is_(True),
                )
            ).all()
        }
        out: list[PracticeExamRead] = []
        for e, s, subject_name in rows:
            a = attempts.get(e.id)
            out.append(PracticeExamRead(
                exam_id=e.id, title=e.title, subject_name=subject_name,
                description=e.description, duration_minutes=e.duration_minutes, question_count=counts.get(e.id, 0),
                times_taken=s.times_taken if s else 0,
                best_percentage=s.best_percentage if s else None,
                last_percentage=s.last_percentage if s else None,
                last_taken_at=s.last_taken_at if s else None,
                in_progress_attempt_id=a.id if a is not None and a.status == AttemptStatus.STARTED else None,
                latest_attempt_id=a.id if a is not None and a.status != AttemptStatus.STARTED else None,
            ))
        return out, total

    @staticmethod
    def start(session: Session, exam_id: UUID, student_id: UUID) -> AttemptModel:
        """Start (or resume) practising an exam. A finished practice attempt is
        reused: its answers are cleared for a fresh go."""
        exam = PracticeService.listed_exam(session, exam_id)
        now = datetime.now(timezone.utc)

        assignment = session.exec(
            select(ExamAssignment).where(
                ExamAssignment.exam_id == exam.id, ExamAssignment.student_id == student_id,
                col(ExamAssignment.practice).is_(True),
            ).with_for_update()
        ).first()
        if assignment is None:
            assignment = ExamAssignment(
                exam_id=exam.id, student_id=student_id, org_id=exam.org_id, assigned_by=student_id,
                status=AssignmentStatus.STARTED, practice=True,
            )
            session.add(assignment)
            session.flush()

        attempt = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == exam.id, AttemptModel.student_id == student_id,
                col(AttemptModel.practice).is_(True),
            ).with_for_update()
        ).first()

        if attempt is not None and attempt.status == AttemptStatus.STARTED:
            deadline = AttemptService._deadline(attempt, exam)
            if deadline is None or now < deadline:
                session.commit()
                return attempt                                  # carry on
            attempt = AttemptService._finalize_submission(session, attempt, exam, auto=True)

        if attempt is None:
            attempt = AttemptModel(
                exam_id=exam.id, student_id=student_id, org_id=exam.org_id, assignment_id=assignment.id,
                status=AttemptStatus.STARTED, attempt_number=0, practice=True, started_at=now,
            )
        else:
            # A fresh go: the previous practice answers are replaced.
            session.exec(delete(ResponseModel).where(col(ResponseModel.attempt_id) == attempt.id))
            session.exec(delete(LateResponseModel).where(col(LateResponseModel.attempt_id) == attempt.id))
            attempt.status = AttemptStatus.STARTED
            attempt.started_at = now
            attempt.clock_started_at = None
            attempt.submitted_at = None
            attempt.raw_score = attempt.final_score = attempt.percentage = None
            attempt.passed = None
            attempt.scored_at = None
            attempt.scored_by = None
            attempt.extra_seconds = 0
            attempt.extra_given_seconds = 0
            attempt.resume_seconds = None
        assignment.status = AssignmentStatus.STARTED
        session.add(assignment)
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        return attempt

    @staticmethod
    def stats(session: Session, exam_id: UUID, current_user: CurrentUser) -> PracticeStatsRead:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.RESULTS_VIEW)
        students, attempts, average = session.exec(
            select(
                func.count(), func.coalesce(func.sum(PracticeSummary.times_taken), 0),
                func.avg(PracticeSummary.last_percentage),
            ).where(PracticeSummary.exam_id == exam.id)
        ).one()
        return PracticeStatsRead(
            students=int(students), attempts=int(attempts),
            average_percentage=round(float(average), 1) if average is not None else None,
        )
