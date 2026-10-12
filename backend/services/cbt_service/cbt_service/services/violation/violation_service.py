from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func, col

from cbt_service.schemas.attempt_schemas import CurrentUser
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
    def _get_attempt_for_student(
        session: Session, attempt_id: UUID, org_id: UUID, student_id: UUID
    ) -> AttemptModel:
        attempt: AttemptModel | None = session.exec(
            select(AttemptModel)
            .where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == org_id,
                AttemptModel.student_id == student_id,
            )
            .with_for_update()  # serializes concurrent violation requests per attempt
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        return attempt

    # @staticmethod
    # def _get_attempt_for_student(session: Session, attempt_id: UUID, org_id: UUID, student_id: UUID) -> AttemptModel:
    #     attempt = session.exec(
    #         select(AttemptModel).where(
    #             AttemptModel.id == attempt_id,
    #             AttemptModel.org_id == org_id,
    #             AttemptModel.student_id == student_id,
    #         )
    #     ).first()
    #     if not attempt:
    #         raise HTTPException(status_code=404, detail="Attempt not found.")
    #     if attempt.status != AttemptStatus.STARTED:
    #         raise HTTPException(status_code=400, detail="Violations can only be logged for an active attempt.")
    #     return attempt


    @staticmethod
    def _count_violations(session: Session, attempt_id: UUID) -> int:
        count: int = session.exec(
            select(func.count())
            .select_from(ExamViolation)
            .where(
                ExamViolation.attempt_id == attempt_id,
                col(ExamViolation.cleared_at).is_(None),
            )
        ).one()
        return count

    @staticmethod
    def _closed_result(
        session: Session, attempt: AttemptModel, exam: ExamModel
    ) -> ViolationCreateResult:
        """Attempt is no longer active: record nothing, just report its final state."""
        return ViolationCreateResult(
            violation=None,
            violation_count=ViolationService._count_violations(session, attempt.id),
            max_violations=exam.max_violations,
            exam_terminated=attempt.status == AttemptStatus.TERMINATED,
            attempt_status=attempt.status,
        )




    @staticmethod
    async def create(
        session: Session,
        attempt_id: UUID,
        payload: ViolationCreate,
        current_user: CurrentUser,
    ) -> ViolationCreateResult:
        # 1. Load AND lock the attempt row (with_for_update inside the helper).
        #    Concurrent violation requests for the same attempt now queue up here,
        #    so each one sees the previous one's committed insert when it counts.
        attempt: AttemptModel = ViolationService._get_attempt_for_student(
            session, attempt_id, current_user.org_id, current_user.id
        )

        exam: ExamModel | None = session.exec(
            select(ExamModel).where(ExamModel.id == attempt.exam_id)
        ).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        # 2. Already closed (terminated, submitted, reset): not an error.
        #    Late or queued client events just get the final state back.
        if attempt.status != AttemptStatus.STARTED:
            return ViolationService._closed_result(session, attempt, exam)

        # 3. Time may have run out since the last request. This auto-submits and
        #    commits if so, which also releases the row lock.
        attempt = await AttemptService._enforce_time_limit(session, attempt)
        if attempt.status != AttemptStatus.STARTED:
            return ViolationService._closed_result(session, attempt, exam)

        # 4. Record the violation (flushed, not yet committed).
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
        session.flush()

        # 5. Count, including the one just flushed.
        violation_count: int = ViolationService._count_violations(session, attempt_id)

        # 6. Terminate if the limit is reached. _finalize_submission commits,
        #    which persists the violation, the scores and the TERMINATED status together.
        exam_terminated = False
        if exam.max_violations is not None and violation_count >= exam.max_violations:
            attempt = await AttemptService._finalize_submission(
                session, attempt, exam, final_status=AttemptStatus.TERMINATED,
            )
            exam_terminated = True
        else:
            session.commit()  # persists the violation and releases the lock

        session.refresh(violation)

        return ViolationCreateResult(
            violation=ViolationRead.model_validate(violation, from_attributes=True),
            violation_count=violation_count,
            max_violations=exam.max_violations,
            exam_terminated=exam_terminated,
            attempt_status=attempt.status,
        )
      


    # @staticmethod
    # async def create(
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
    #     session.flush()  # id + defaults populated, visible to the count query below, not yet committed

    #     violation_count = session.exec(
    #         select(func.count()).select_from(ExamViolation).where(
    #             ExamViolation.attempt_id == attempt_id,
    #         )
    #     ).one()

    #     exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
    #     if not exam:
    #         raise HTTPException(status_code=404, detail="Exam not found.")

    #     exam_terminated = False
    #     if exam.max_violations is not None and violation_count >= exam.max_violations:
    #         # Scores whatever was answered so far and closes the attempt —
    #         # same scoring path as a normal submit, just a different terminal status.
    #         attempt = await AttemptService._finalize_submission(
    #             session, attempt, exam, final_status=AttemptStatus.TERMINATED,
    #         )
    #         exam_terminated = True
    #     else:
    #         session.commit()

    #     session.refresh(violation)

    #     return ViolationCreateResult(
    #         violation=ViolationRead.model_validate(violation, from_attributes=True),
    #         violation_count=violation_count,
    #         max_violations=exam.max_violations,
    #         exam_terminated=exam_terminated,
    #     )


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