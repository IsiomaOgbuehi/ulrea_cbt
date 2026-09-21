from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func

from cbt_service.database.models.exam import ExamModel
from cbt_service.services.attempt_service import AttemptService
from cbt_service.database.models.violation import ExamViolation
from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.schemas.violation_schemas import ViolationCreate, ViolationCreateResult, ViolationRead
from cbt_service.services.exam_service import ExamService


class ViolationService:

    @staticmethod
    def _get_attempt_for_student(session: Session, attempt_id: UUID, org_id: UUID, student_id: UUID) -> AttemptModel:
        attempt = session.exec(
            select(AttemptModel).where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == org_id,
                AttemptModel.student_id == student_id,
            )
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        if attempt.status != AttemptStatus.STARTED:
            raise HTTPException(status_code=400, detail="Violations can only be logged for an active attempt.")
        return attempt

    # @staticmethod
    # def create(
    #     session: Session,
    #     attempt_id: UUID,
    #     payload: ViolationCreate,
    #     current_user,
    # ) -> ViolationCreateResult:
    #     attempt = ViolationService._get_attempt_for_student(
    #         session, attempt_id, current_user.org_id, current_user.id
    #     )

    #     violation = ExamViolation(
    #         org_id=current_user.org_id,
    #         exam_id=attempt.exam_id,
    #         attempt_id=attempt.id,
    #         student_id=current_user.id,
    #         violation_type=payload.violation_type,
    #         severity=payload.severity,
    #         description=payload.description,
    #         metadata_=payload.metadata_,
    #         occurred_at=payload.occurred_at,
    #     )
    #     session.add(violation)
    #     session.flush()  # get violation.id without a separate round trip; not yet committed

    #     # Count including the one just added — flush() makes it visible to this query
    #     violation_count = session.exec(
    #         select(func.count()).where(
    #             ExamViolation.attempt_id == attempt_id,
    #         ).select_from(ExamViolation)
    #     ).one()

    #     exam = ExamService._get_exam(session, attempt.exam_id, current_user.org_id)

    #     exam_terminated = False
    #     if exam.max_violations is not None and violation_count >= exam.max_violations:
    #         attempt.status = AttemptStatus.TERMINATED
    #         attempt.submitted_at = datetime.now(timezone.utc)
    #         session.add(attempt)
    #         exam_terminated = True

    #     session.commit()
    #     session.refresh(violation)

    #     return ViolationCreateResult(
    #         violation=violation,
    #         violation_count=violation_count,
    #         max_violations=exam.max_violations,
    #         exam_terminated=exam_terminated,
    #     )


    @staticmethod
    async def create(
        session: Session,
        attempt_id: UUID,
        payload: ViolationCreate,
        current_user,
    ) -> ViolationCreateResult:
        attempt = ViolationService._get_attempt_for_student(
            session, attempt_id, current_user.org_id, current_user.id
        )

        violation = ExamViolation(
            org_id=current_user.org_id,
            exam_id=attempt.exam_id,
            attempt_id=attempt.id,
            student_id=current_user.id,
            violation_type=payload.violation_type,
            severity=payload.severity,
            description=payload.description,
            metadata_=payload.metadata_,
            occurred_at=payload.occurred_at,
        )
        session.add(violation)
        session.flush()  # id + defaults populated, visible to the count query below, not yet committed

        violation_count = session.exec(
            select(func.count()).select_from(ExamViolation).where(
                ExamViolation.attempt_id == attempt_id,
            )
        ).one()

        exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        exam_terminated = False
        if exam.max_violations is not None and violation_count >= exam.max_violations:
            # Scores whatever was answered so far and closes the attempt —
            # same scoring path as a normal submit, just a different terminal status.
            attempt = await AttemptService._finalize_submission(
                session, attempt, exam, final_status=AttemptStatus.TERMINATED,
            )
            exam_terminated = True
        else:
            session.commit()

        session.refresh(violation)

        return ViolationCreateResult(
            violation=ViolationRead.model_validate(violation, from_attributes=True),
            violation_count=violation_count,
            max_violations=exam.max_violations,
            exam_terminated=exam_terminated,
        )


    @staticmethod
    def list_for_attempt(session: Session, attempt_id: UUID, current_user) -> list[ExamViolation]:
        attempt = session.exec(
            select(AttemptModel).where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == current_user.org_id,
            )
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        # Reuse the exam's own view-access rule (creator / subject-assigned teacher / admin)
        exam = ExamService._get_exam(session, attempt.exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)

        return session.exec(
            select(ExamViolation)
            .where(ExamViolation.attempt_id == attempt_id)
            .order_by(ExamViolation.occurred_at)
        ).all()

    @staticmethod
    def list_for_exam(session: Session, exam_id: UUID, current_user) -> list[ExamViolation]:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)

        return session.exec(
            select(ExamViolation)
            .where(ExamViolation.exam_id == exam_id)
            .order_by(ExamViolation.occurred_at)
        ).all()

    @staticmethod
    def mark_reviewed(session: Session, violation_id: UUID, current_user) -> ExamViolation:
        violation = session.exec(
            select(ExamViolation).where(
                ExamViolation.id == violation_id,
                ExamViolation.org_id == current_user.org_id,
            )
        ).first()
        if not violation:
            raise HTTPException(status_code=404, detail="Violation not found.")

        exam = ExamService._get_exam(session, violation.exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)

        violation.reviewed = True
        violation.reviewed_by = current_user.id
        violation.reviewed_at = datetime.now(timezone.utc)
        session.add(violation)
        session.commit()
        session.refresh(violation)
        return violation