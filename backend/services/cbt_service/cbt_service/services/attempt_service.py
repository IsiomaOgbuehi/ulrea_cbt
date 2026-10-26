import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import Session, delete, select, col, delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from pydantic import BaseModel
from typing import Any
import random
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from io import BytesIO
from fastapi.responses import StreamingResponse


from cbt_service.database.models.violation import ExamViolation
from cbt_service.database.models.enums.exam_enum import ExamStatus
from cbt_service.schemas.item_subject_schemas import ItemForDisplayRead, ItemForScoringRead
from cbt_service.services.item.item_service import ItemService
from cbt_service.database.models.exam import ExamAssignment, ExamModel
from cbt_service.services.exam_service import ExamService, _log
from cbt_service.database.models.attempt import AttemptModel, ResponseModel

from cbt_service.schemas.attempt_schemas import (
    AttemptDetailRead,
    AttemptExamRead,
    AttemptItemRead,
    AttemptResponseDetail,
    AttemptSectionRead,
    CohortAttemptSummary,
    ResetAttemptRequest,
    StartAttemptRequest,
    SaveResponseRequest,
    ManualReviewRequest,
    AttemptRead,
    AttemptStatusRead,
    ResponseRead,
    CurrentUser,
)
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.core.permissions import P


@dataclass
class ExamExportData:
    exam_items: list[Any]
    attempts: list[AttemptModel]
    responses: list[ResponseModel]

# ============================================================
# SERVICE
# ============================================================

class AttemptService:

    @staticmethod
    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @staticmethod
    def start(session: Session, payload: StartAttemptRequest, student_id: UUID, org_id: UUID) -> AttemptModel:
        exam = session.exec(
            select(ExamModel).where(
                ExamModel.id == payload.exam_id,
                ExamModel.org_id == org_id,
            )
        ).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        existing = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == payload.exam_id,
                AttemptModel.student_id == student_id,
            )
        ).all()

        # assignment = session.exec(
        #     select(ExamAssignment).where(ExamAssignment.id == payload.assignment_id)
        # ).first()
        assignment = session.exec(
            select(ExamAssignment).where(
                ExamAssignment.id == payload.assignment_id,
                ExamAssignment.exam_id == payload.exam_id,
                ExamAssignment.student_id == student_id,
                ExamAssignment.org_id == org_id,
            )
        ).first()
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found.")

        effective_start, effective_end = ExamService.get_effective_window(exam, assignment)
        now = datetime.now(timezone.utc)

        if effective_start and now < effective_start:
            raise HTTPException(status_code=400, detail="This exam is not yet available.")
        if effective_end and now > effective_end:
            raise HTTPException(status_code=400, detail="The window for this exam has closed.")

        in_progress = next((a for a in existing if a.status == AttemptStatus.STARTED), None)
        if in_progress:
            return in_progress

        # Reactivate a reset attempt in place, instead of creating a new row.
        # A reset is meant to give the SAME attempt a clean restart, not free
        # up a slot for a separate attempt record — otherwise you end up with
        # one permanently-RESET row and a second independent SUBMITTED row.
        reset_attempts = [a for a in existing if a.status == AttemptStatus.RESET]
        if reset_attempts:
            # If more than one has ever been reset, resume the most recent.
            attempt_to_resume = max(reset_attempts, key=lambda a: a.started_at)
            attempt_to_resume.status = AttemptStatus.STARTED
            attempt_to_resume.started_at = datetime.now(timezone.utc)
            attempt_to_resume.submitted_at = None
            attempt_to_resume.raw_score = None
            attempt_to_resume.final_score = None
            attempt_to_resume.percentage = None
            attempt_to_resume.passed = None
            attempt_to_resume.scored_at = None
            attempt_to_resume.scored_by = None
            session.add(attempt_to_resume)
            session.commit()
            session.refresh(attempt_to_resume)
            return attempt_to_resume

        max_attempts = exam.max_attempts or 1
        countable = [a for a in existing if a.status != AttemptStatus.RESET]
        if len(countable) >= max_attempts:
            raise HTTPException(
                status_code=400,
                detail=f"Maximum attempts ({max_attempts}) reached for this exam.",
            )

        attempt = AttemptModel(
            exam_id=payload.exam_id,
            student_id=student_id,
            org_id=org_id,
            assignment_id=payload.assignment_id,
            attempt_number=len(existing) + 1,
        )
        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        return attempt


    # @staticmethod
    # def save_response_new(
    #     session: Session, attempt_id: UUID, payload: SaveResponseRequest, student_id: UUID
    # ) -> ResponseModel:
    #     attempt = AttemptService.get_attempt(session, attempt_id, student_id)
    #     if attempt.status != AttemptStatus.STARTED:
    #         raise HTTPException(
    #             status_code=409,
    #             detail={"code": "ATTEMPT_CLOSED", "status": attempt.status.value},
    #         )

    #     now = datetime.now(timezone.utc)
    #     stmt = (
    #         pg_insert(ResponseModel)
    #         .values(
    #             id=uuid4(),  # SQLModel default_factory is NOT applied on Core inserts
    #             attempt_id=attempt_id,
    #             item_id=payload.item_id,
    #             exam_item_id=payload.exam_item_id,
    #             org_id=attempt.org_id,
    #             answer=payload.answer,
    #             time_spent_seconds=payload.time_spent_seconds,
    #             is_flagged=payload.is_flagged,
    #             updated_at=now,
    #         )
    #         .on_conflict_do_update(
    #             index_elements=["attempt_id", "item_id"],
    #             set_={
    #                 "answer": payload.answer,
    #                 "time_spent_seconds": payload.time_spent_seconds,
    #                 "is_flagged": payload.is_flagged,
    #                 "updated_at": now,
    #             },
    #         )
    #         .returning(ResponseModel)
    #     )
    #     response: ResponseModel = session.exec(stmt).scalar_one()
    #     session.commit()
    #     return response

    @staticmethod
    def save_response(
        session: Session,
        attempt_id: UUID,
        payload: SaveResponseRequest,
        student_id: UUID,
    ) -> ResponseModel:
        attempt = AttemptService.get_attempt(session, attempt_id, student_id)  # expiry-checked

        if attempt.status != AttemptStatus.STARTED:
            raise HTTPException(
            status_code=409,
            detail={"code": "ATTEMPT_CLOSED", "status": attempt.status.value},
        )

        existing = session.exec(
            select(ResponseModel).where(
                ResponseModel.attempt_id == attempt_id,
                ResponseModel.item_id == payload.item_id,
            )
        ).first()

        if existing:
            existing.answer = payload.answer
            existing.time_spent_seconds = payload.time_spent_seconds
            existing.is_flagged = payload.is_flagged
            existing.updated_at = datetime.now(timezone.utc)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        response = ResponseModel(
            attempt_id=attempt_id,
            item_id=payload.item_id,
            exam_item_id=payload.exam_item_id,
            org_id=attempt.org_id,
            answer=payload.answer,
            time_spent_seconds=payload.time_spent_seconds,
            is_flagged=payload.is_flagged,
        )
        session.add(response)
        session.commit()
        session.refresh(response)
        return response
    

    @staticmethod
    def submit(
        session: Session,
        attempt_id: UUID,
        student_id: UUID,
        pass_mark: float | None = None,
    ) -> AttemptModel:
        attempt = AttemptService.get_attempt(session, attempt_id, student_id)  # expiry-checked

        if attempt.status != AttemptStatus.STARTED:
            return attempt
            # raise HTTPException(status_code=400, detail="Attempt already submitted.")

        exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        return AttemptService._finalize_submission(session, attempt, exam, pass_mark=pass_mark, auto=False)
    

    @staticmethod
    def manual_review(
        session: Session,
        response_id: UUID,
        payload: ManualReviewRequest,
        reviewer_id: UUID,
        current_user: CurrentUser | None = None,
    ) -> ResponseModel:
        resp = session.exec(
            select(ResponseModel).where(ResponseModel.id == response_id)
        ).first()

        if not resp:
            raise HTTPException(status_code=404, detail="Response not found.")

        if current_user is not None:
            # Only answers to exams the marker can reach, in their own organisation.
            owner = session.exec(
                select(AttemptModel).where(
                    AttemptModel.id == resp.attempt_id,
                    AttemptModel.org_id == current_user.org_id,
                )
            ).first()
            if owner is None:
                raise HTTPException(status_code=404, detail="Response not found.")
            exam_for_check: ExamModel = ExamService._get_exam(session, owner.exam_id, current_user.org_id)
            ExamService._assert_can_view(session, exam_for_check, current_user, P.RESULTS_MARK)

        resp.marks_awarded = payload.marks_awarded
        resp.is_correct = payload.marks_awarded > 0
        resp.reviewed_by = reviewer_id
        resp.review_notes = payload.review_notes
        session.add(resp)

        # Recalculate attempt score
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == resp.attempt_id)
        ).first()

        if attempt:
            all_responses = session.exec(
                select(ResponseModel).where(ResponseModel.attempt_id == attempt.id)
            ).all()
            attempt.final_score = max(0.0, sum(
                (r.marks_awarded or 0.0) for r in all_responses
            ))
            attempt.scored_by = str(reviewer_id)
            session.add(attempt)

        session.commit()
        session.refresh(resp)
        return resp

    @staticmethod
    def get_attempt(session: Session, attempt_id: UUID, user_id: UUID) -> AttemptModel:
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == attempt_id)
        ).first()
        if not attempt or attempt.student_id != user_id:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        return AttemptService._enforce_time_limit(session, attempt)
        # return attempt

    @staticmethod
    def get_attempt_status(
        session: Session, attempt_id: UUID, student_id: UUID
    ) -> AttemptStatusRead:
        """
        Heartbeat poll. Loads the attempt, its exam and the live violation
        count in ONE query (previously 5 round trips, 3 of them for the same
        exam row). Still enforces expiry: an attempt past its deadline is
        auto-submitted, exactly as get_attempt() does.
        """
        # Violations count for this attempt, computed inside the same query.
        # (Inline rather than ViolationService._count_violations to avoid a
        # circular import: violation_service imports this module.)
        violation_count = (
            select(func.count())
            .select_from(ExamViolation)
            .where(
                ExamViolation.attempt_id == AttemptModel.id,
                col(ExamViolation.cleared_at).is_(None),
            )
            .correlate(AttemptModel)
            .scalar_subquery()
        )

        row = session.exec(
            select(AttemptModel, ExamModel, violation_count)
            .outerjoin(ExamModel, col(ExamModel.id) == AttemptModel.exam_id)
            .where(
                AttemptModel.id == attempt_id,
                AttemptModel.student_id == student_id,  # ownership: 404 otherwise
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        attempt: AttemptModel = row[0]
        exam: ExamModel | None = row[1]
        count: int = row[2]

        if exam is not None:
            # Only writes (auto-submit) when the deadline has passed.
            attempt = AttemptService._enforce_time_limit_new(session, attempt, exam)

        read: AttemptRead = AttemptService._to_attempt_read_new(attempt, exam)
        return AttemptStatusRead(
            status=attempt.status,
            remaining_seconds=read.remaining_seconds,
            violation_count=count,
            max_violations=exam.max_violations if exam else None,
            server_time=read.server_time,
        )

    @staticmethod
    def get_responses(session: Session, attempt_id: UUID, user_id: UUID) -> list[ResponseModel]:
        AttemptService.get_attempt(session, attempt_id, user_id)  # expiry-checked
        return session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()
    


    @staticmethod
    def get_exam_content(session: Session, attempt_id: UUID, student_id: UUID) -> AttemptExamRead:
        attempt = AttemptService.get_attempt(session, attempt_id, student_id)

        exam = session.exec(
            select(ExamModel).where(ExamModel.id == attempt.exam_id)
        ).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        sections_raw = ExamService.get_sections_with_items_internal(session, attempt.exam_id)

        all_item_ids = [
            item.item_id
            for section in sections_raw
            for item in section.items
        ]

        # item_bank_client = ItemBankClient()
        # items_by_id = await item_bank_client.get_items_for_display(all_item_ids)
        items = ItemService.get_by_ids_for_display(session, all_item_ids)
        # items_by_id = [ItemForDisplayRead.model_validate(i, from_attributes=True) for i in items]
        items_by_id = {
            item.id: ItemForDisplayRead.model_validate(item, from_attributes=True).model_dump()
            for item in items
        }

        sections = []
        for section in sections_raw:
            items = []
            for exam_item in section.items:
                item_id = exam_item.item_id
                content = items_by_id.get(item_id)
                if content is None:
                    continue
                items.append(AttemptItemRead(
                    id=item_id,
                    exam_item_id=exam_item.id,
                    question_text=content["question_text"],
                    item_type=content["item_type"],
                    options=content.get("options"),
                    marks=exam_item.marks,
                ))

            if exam.shuffle_options:
                items = AttemptService._shuffle_options(items, attempt_id)

            sections.append(AttemptSectionRead(
                id=section.id,
                title=section.title,
                items=items,
            ))

        if exam.shuffle_questions:
            sections = AttemptService._shuffle_sections(sections, attempt_id)

        return AttemptExamRead(
            attempt_id=attempt.id,
            exam_id=attempt.exam_id,
            duration_minutes=exam.duration_minutes,
            shuffle_questions=exam.shuffle_questions,
            shuffle_options=exam.shuffle_options,
            sections=sections,
        )

    

    # @staticmethod
    # def get_exam_content(session: Session, attempt_id: UUID, student_id: UUID) -> AttemptExamRead:
    #     attempt, exam = AttemptService.get_attempt_and_exam(session, attempt_id, student_id)

    #     sections_raw = ExamService.get_sections_with_items_internal(session, attempt.exam_id)
    #     all_item_ids: list[UUID] = [i.item_id for s in sections_raw for i in s.items]

    #     db_items = ItemService.get_by_ids_for_display(session, all_item_ids)
    #     items_by_id: dict[UUID, dict[str, Any]] = {
    #         it.id: ItemForDisplayRead.model_validate(it, from_attributes=True).model_dump()
    #         for it in db_items
    #     }

    #     sections: list[AttemptSectionRead] = []
    #     for section in sections_raw:
    #         section_items: list[AttemptItemRead] = []
    #         for exam_item in section.items:
    #             content = items_by_id.get(exam_item.item_id)
    #             if content is None:
    #                 continue
    #             section_items.append(AttemptItemRead(
    #                 id=exam_item.item_id,
    #                 exam_item_id=exam_item.id,
    #                 question_text=content["question_text"],
    #                 item_type=content["item_type"],
    #                 options=content.get("options"),
    #                 marks=exam_item.marks,
    #             ))
    #         if exam.shuffle_options:
    #             section_items = AttemptService._shuffle_options(section_items, attempt_id)
    #         sections.append(AttemptSectionRead(id=section.id, title=section.title, items=section_items))

    #     if exam.shuffle_questions:
    #         sections = AttemptService._shuffle_sections(sections, attempt_id)

    #     return AttemptExamRead(
    #         attempt_id=attempt.id, exam_id=attempt.exam_id,
    #         duration_minutes=exam.duration_minutes,
    #         shuffle_questions=exam.shuffle_questions,
    #         shuffle_options=exam.shuffle_options,
    #         sections=sections,
    #     )


    @staticmethod
    def _shuffle_sections(sections: list[AttemptSectionRead], attempt_id: UUID) -> list[AttemptSectionRead]:
        # Seed with attempt_id so the order is stable across repeated
        # GETs for the same attempt (autosave, page refresh, etc.)
        # but differs per student.
        rng = random.Random(str(attempt_id))
        shuffled = sections.copy()
        rng.shuffle(shuffled)
        return shuffled

    @staticmethod
    def _shuffle_options(items: list[AttemptItemRead], attempt_id: UUID) -> list[AttemptItemRead]:
        rng = random.Random(str(attempt_id))
        for item in items:
            if item.options:
                rng.shuffle(item.options)
        return items


    # @staticmethod
    # def get_cohort_attempts(
    #     session: Session,
    #     exam_id: UUID,
    #     cohort_id: UUID,
    #     current_user: CurrentUser,
    # ) -> list[CohortAttemptSummary]:
    #     attempts = session.exec(
    #         select(AttemptModel).where(
    #             AttemptModel.exam_id == exam_id,
    #             AttemptModel.org_id == current_user.org_id,
    #         )
    #     ).all()

    #     # Filter to the cohort via assignment lookup, since AttemptModel stores assignment_id
    #     assignment_ids = {a.assignment_id for a in attempts}
    #     cohort_assignments = session.exec(
    #         select(ExamAssignment.id).where(
    #             ExamAssignment.id.in_(assignment_ids),
    #             ExamAssignment.cohort_id == cohort_id,
    #         )
    #     ).all()
    #     cohort_assignment_ids = set(cohort_assignments)

    #     return [
    #         CohortAttemptSummary(
    #             id=a.id, student_id=a.student_id, status=a.status,
    #             submitted_at=a.submitted_at, final_score=a.final_score,
    #             percentage=a.percentage, passed=a.passed,
    #         )
    #         for a in attempts if a.assignment_id in cohort_assignment_ids
    #     ]
    @staticmethod
    def get_cohort_attempts(
        session: Session,
        exam_id: UUID,
        cohort_id: UUID,
        current_user: CurrentUser,
    ) -> list[CohortAttemptSummary]:
        exam: ExamModel = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.RESULTS_VIEW)

        attempts: list[AttemptModel] = list(
            session.exec(
                select(AttemptModel)
                .join(ExamAssignment, ExamAssignment.id == AttemptModel.assignment_id)
                .where(
                    AttemptModel.exam_id == exam_id,
                    AttemptModel.org_id == current_user.org_id,
                    ExamAssignment.cohort_id == cohort_id,
                )
            ).all()
        )

        summaries: list[CohortAttemptSummary] = [
            CohortAttemptSummary(
                id=a.id, student_id=a.student_id, status=a.status,
                submitted_at=a.submitted_at, final_score=a.final_score,
                percentage=a.percentage, passed=a.passed,
            )
            for a in attempts
        ]

        # Done with the DB: return the connection to the pool now, so it isn't
        # held while the router waits on the auth service.
        session.close()
        return summaries
    

    @staticmethod
    def _get_items_for_scoring(session: Session, item_ids: list[UUID]) -> dict[UUID, dict[str, Any]]: # list[ItemForScoringRead]:
        items = ItemService.get_by_ids_for_scoring(session, item_ids)
        # return [ItemForScoringRead.model_validate(i, from_attributes=True) for i in items]
        return {
            item.id: ItemForScoringRead.model_validate(item, from_attributes=True).model_dump()
            for item in items
        }



    @staticmethod
    def get_attempt_detail_for_staff(
        session: Session,
        attempt_id: UUID,
        current_user: CurrentUser,
    ) -> AttemptDetailRead:
        attempt = session.exec(
            select(AttemptModel).where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == current_user.org_id,
            )
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        ExamService._assert_can_view(
            session, ExamService._get_exam(session, attempt.exam_id, current_user.org_id),
            current_user, P.RESULTS_VIEW,
        )

        responses = session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()

        item_ids = [r.item_id for r in responses]
        # item_bank_client = ItemBankClient()
        # item_bank = await item_bank_client.get_items_for_scoring(item_ids)  # teacher view — answer keys OK here

        # items = ItemService.get_by_ids_for_scoring(session, item_ids)
        item_bank = AttemptService._get_items_for_scoring(session, item_ids)

        response_details = [
            AttemptResponseDetail(
                item_id=r.item_id,
                question_text=item_bank.get(r.item_id, {}).get("question_text", "[item unavailable]"),
                answer=r.answer,
                correct_answers=item_bank.get(r.item_id, {}).get("correct_answers"),
                is_correct=r.is_correct,
                marks_awarded=r.marks_awarded,
            )
            for r in responses
        ]

        return AttemptDetailRead(
            id=attempt.id, student_id=attempt.student_id, exam_id=attempt.exam_id,
            status=attempt.status, started_at=attempt.started_at, submitted_at=attempt.submitted_at,
            raw_score=attempt.raw_score, final_score=attempt.final_score,
            percentage=attempt.percentage, passed=attempt.passed,
            responses=response_details,
        )



    @staticmethod
    def reset_attempt(
        session: Session,
        attempt_id: UUID,
        payload: ResetAttemptRequest,
        current_user: CurrentUser,
    ) -> AttemptModel:
        # Lock the row so a violation or autosave arriving mid-reset can't
        # interleave with the clean-up below.
        attempt: AttemptModel | None = session.exec(
            select(AttemptModel)
            .where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == current_user.org_id,
            )
            .with_for_update()
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        # 1. Wipe responses so the student gets a clean slate.
        session.exec(
            delete(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        )

        # 2. Soft-clear violations: keep the rows as evidence, but stop them
        #    counting toward max_violations. Already-cleared rows keep their
        #    original cleared_at.
        active_violation_filter = (
            ExamViolation.attempt_id == attempt_id,
            col(ExamViolation.cleared_at).is_(None),
        )
        violations_cleared: int = session.exec(
            select(func.count()).select_from(ExamViolation).where(*active_violation_filter)
        ).one()

        if violations_cleared:
            session.exec(
                update(ExamViolation)
                .where(*active_violation_filter)
                .values(cleared_at=datetime.now(timezone.utc))
            )

        # 3. Reset the attempt itself. start() reactivates this same row.
        attempt.status = AttemptStatus.RESET
        attempt.submitted_at = None
        attempt.raw_score = None
        attempt.final_score = None
        attempt.percentage = None
        attempt.passed = None
        attempt.scored_at = None
        attempt.scored_by = None
        session.add(attempt)

        # 4. Optionally reschedule the assignment.
        if payload.new_scheduled_at:
            assignment: ExamAssignment | None = session.exec(
                select(ExamAssignment).where(ExamAssignment.id == attempt.assignment_id)
            ).first()
            if assignment:
                exam: ExamModel | None = session.exec(
                    select(ExamModel).where(ExamModel.id == attempt.exam_id)
                ).first()
                assignment.scheduled_at = payload.new_scheduled_at
                assignment.deadline_override = (
                    payload.new_scheduled_at + timedelta(minutes=exam.duration_minutes)
                    if exam else None
                )
                session.add(assignment)

        # 5. Audit trail, including how many violations were cleared.
        _log(
            session,
            attempt.exam_id,
            current_user.org_id,
            current_user.id,
            "attempt_reset",
            {
                "attempt_id": str(attempt_id),
                "reason": payload.reason,
                "violations_cleared": violations_cleared,
            },
        )

        # Single commit: responses, violations, attempt, assignment and log
        # all succeed or all roll back together.
        session.commit()
        session.refresh(attempt)
        return attempt


    # @staticmethod
    # def reset_attempt(
    #     session: Session,
    #     attempt_id: UUID,
    #     payload: ResetAttemptRequest,
    #     current_user: CurrentUser,
    # ) -> AttemptModel:
    #     attempt = session.exec(
    #         select(AttemptModel).where(
    #             AttemptModel.id == attempt_id,
    #             AttemptModel.org_id == current_user.org_id,
    #         )
    #     ).first()
    #     if not attempt:
    #         raise HTTPException(status_code=404, detail="Attempt not found.")

    #     # Wipe responses so the student gets a clean slate
    #     session.exec(
    #         delete(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
    #     )

    #     attempt.status = AttemptStatus.RESET
    #     attempt.submitted_at = None
    #     attempt.raw_score = None
    #     attempt.final_score = None
    #     attempt.percentage = None
    #     attempt.passed = None
    #     attempt.scored_at = None
    #     attempt.scored_by = None
    #     session.add(attempt)

    #     if payload.new_scheduled_at:
    #         assignment = session.exec(
    #             select(ExamAssignment).where(ExamAssignment.id == attempt.assignment_id)
    #         ).first()
    #         if assignment:
    #             exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
    #             assignment.scheduled_at = payload.new_scheduled_at
    #             assignment.deadline_override = (
    #                 payload.new_scheduled_at + timedelta(minutes=exam.duration_minutes)
    #                 if exam else None
    #             )
    #             session.add(assignment)

    #     _log(session, attempt.exam_id, current_user.org_id, current_user.id,
    #         "attempt_reset", {"attempt_id": str(attempt_id), "reason": payload.reason})

    #     session.commit()
    #     session.refresh(attempt)
    #     return attempt





    @staticmethod
    def _finalize_submission(
        session: Session,
        attempt: AttemptModel,
        exam: ExamModel,
        pass_mark: float | None = None,
        auto: bool = False,
        final_status: AttemptStatus = AttemptStatus.SUBMITTED,   # NEW
    ) -> AttemptModel:
        """
        Scores and closes out an attempt. Shared by student-triggered submit,
        lazy time-expiry auto-submit, and violation-triggered termination —
        all three score identically; only the resulting status differs.
        """
        responses = session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt.id)
        ).all()

        item_ids = [r.item_id for r in responses]
        item_bank = AttemptService._get_items_for_scoring(session, item_ids)

        raw_score = 0.0
        final_score = 0.0
        total_possible = sum(v.get("marks", 1.0) for v in item_bank.values())

        for resp in responses:
            item_data = item_bank.get(resp.item_id)
            if not item_data:
                continue

            item_type = item_data.get("item_type")
            correct = item_data.get("correct_answers", [])
            marks = item_data.get("marks", 1.0)
            neg = item_data.get("negative_marks", 0.0)

            if item_type in ("mcq_single", "mcq_multi", "true_false", "numeric"):
                student_answer = sorted(a.strip().lower() for a in (resp.answer or []))
                correct_answer = sorted(a.strip().lower() for a in (correct or []))

                if student_answer == correct_answer:
                    resp.is_correct = True
                    resp.marks_awarded = marks
                    raw_score += marks
                    final_score += marks
                elif resp.answer:
                    resp.is_correct = False
                    resp.marks_awarded = -neg
                    final_score -= neg
                else:
                    resp.is_correct = False
                    resp.marks_awarded = 0.0
            else:
                resp.is_correct = None
                resp.marks_awarded = None

            session.add(resp)

        attempt.status = final_status
        attempt.submitted_at = datetime.now(timezone.utc)
        attempt.raw_score = raw_score
        attempt.final_score = max(0.0, final_score)
        attempt.percentage = round((attempt.final_score / total_possible * 100), 2) if total_possible else 0.0
        attempt.scored_at = datetime.now(timezone.utc)
        attempt.scored_by = (
            "violation_termination" if final_status == AttemptStatus.TERMINATED
            else "auto_expired" if auto
            else "auto"
        )

        effective_pass_mark = pass_mark if pass_mark is not None else exam.pass_mark
        if effective_pass_mark is not None:
            attempt.passed = attempt.final_score >= effective_pass_mark

        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        return attempt



    @staticmethod
    def _enforce_time_limit(session: Session, attempt: AttemptModel) -> AttemptModel:
        if attempt.status != AttemptStatus.STARTED:
            return attempt

        exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
        if not exam:
            return attempt

        started_at = attempt.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        deadline = started_at + timedelta(minutes=exam.duration_minutes)
        if datetime.now(timezone.utc) < deadline:
            return attempt

        return AttemptService._finalize_submission(session, attempt, exam, auto=True)


    @staticmethod
    def _enforce_time_limit_new(
        session: Session, attempt: AttemptModel, exam: ExamModel
    ) -> AttemptModel:
        if attempt.status != AttemptStatus.STARTED:
            return attempt
        deadline = AttemptService._aware(attempt.started_at) + timedelta(
            minutes=exam.duration_minutes
        )
        if datetime.now(timezone.utc) < deadline:
            return attempt
        return AttemptService._finalize_submission(session, attempt, exam, auto=True)


    @staticmethod
    def _to_attempt_read(session: Session, attempt: AttemptModel) -> AttemptRead:
        exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
        now = datetime.now(timezone.utc)

        started_at = attempt.started_at if attempt.started_at.tzinfo else attempt.started_at.replace(tzinfo=timezone.utc)
        expires_at = started_at + timedelta(minutes=exam.duration_minutes) if exam else started_at

        remaining = (
            max(0, int((expires_at - now).total_seconds()))
            if attempt.status == AttemptStatus.STARTED
            else 0
        )

        return AttemptRead(
            id=attempt.id, exam_id=attempt.exam_id, assignment_id=attempt.assignment_id, student_id=attempt.student_id,
            status=attempt.status, attempt_number=attempt.attempt_number,
            started_at=attempt.started_at, submitted_at=attempt.submitted_at,
            raw_score=attempt.raw_score, final_score=attempt.final_score,
            percentage=attempt.percentage, passed=attempt.passed,
            expires_at=expires_at, remaining_seconds=remaining, server_time=now,
        )

    @staticmethod
    def _to_attempt_read_new(attempt: AttemptModel, exam: ExamModel | None) -> AttemptRead:
        now = datetime.now(timezone.utc)
        started_at = AttemptService._aware(attempt.started_at)
        expires_at = (
            started_at + timedelta(minutes=exam.duration_minutes) if exam else started_at
        )
        remaining = (
            max(0, int((expires_at - now).total_seconds()))
            if attempt.status == AttemptStatus.STARTED
            else 0
        )
        return AttemptRead(
            id=attempt.id, exam_id=attempt.exam_id, assignment_id=attempt.assignment_id,
            student_id=attempt.student_id, status=attempt.status,
            attempt_number=attempt.attempt_number, started_at=attempt.started_at,
            submitted_at=attempt.submitted_at, raw_score=attempt.raw_score,
            final_score=attempt.final_score, percentage=attempt.percentage,
            passed=attempt.passed, expires_at=expires_at,
            remaining_seconds=remaining, server_time=now,
        )


    @staticmethod
    def sweep_expired_attempts(session: Session) -> int:
        now = datetime.now(timezone.utc)
        candidates = session.exec(
            select(AttemptModel).where(AttemptModel.status == AttemptStatus.STARTED)
        ).all()
        expired = 0
        for attempt in candidates:
            exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
            if not exam:
                continue
            started_at = attempt.started_at if attempt.started_at.tzinfo else attempt.started_at.replace(tzinfo=timezone.utc)
            if now >= started_at + timedelta(minutes=exam.duration_minutes):
                asyncio.run(AttemptService._finalize_submission(session, attempt, exam, auto=True))
                expired += 1
        return expired








    @staticmethod
    def load_exam_export_data(
        session: Session,
        exam_id: UUID,
        org_id: UUID,
        current_user: CurrentUser | None = None,
    ) -> ExamExportData:
        """Load the exam, attempts, and responses using SQLModel."""

        exam = session.exec(
            select(ExamModel).where(
                ExamModel.id == exam_id,
                ExamModel.org_id == org_id,
            )
        ).first()

        if exam is None:
            raise HTTPException(
                status_code=404,
                detail="Exam not found.",
            )
        if current_user is not None:
            ExamService._assert_can_view(session, exam, current_user, P.RESULTS_EXPORT)

        sections = ExamService.get_sections_with_items_internal(
            session,
            exam_id,
        )

        exam_items = [
            item
            for section in sections
            for item in section.items
        ]

        attempts = list(
            session.exec(
                select(AttemptModel).where(
                    AttemptModel.exam_id == exam_id,
                    AttemptModel.org_id == org_id,
                )
            ).all()
        )

        if not attempts:
            raise HTTPException(
                status_code=404,
                detail="No attempts found for this exam.",
            )

        attempt_ids = [attempt.id for attempt in attempts]

        responses = list(
            session.exec(
                select(ResponseModel).where(
                    ResponseModel.attempt_id.in_(attempt_ids),
                    ResponseModel.org_id == org_id,
                )
            ).all()
        )

        return ExamExportData(
            exam_items=exam_items,
            attempts=attempts,
            responses=responses,
        )


def answer_is_present(answer: Any) -> bool:
    """Check for an answer without rejecting valid 0 or False values."""

    if answer is None:
        return False

    if isinstance(answer, str):
        return bool(answer.strip())

    if isinstance(answer, (list, dict, tuple, set)):
        return len(answer) > 0

    return True


def build_exam_attempts_workbook(
    export_data: ExamExportData,
    users_by_id: dict,
) -> BytesIO:
    """Create the Excel workbook from loaded exam and student data."""

    exam_items = export_data.exam_items
    attempts = export_data.attempts
    responses = export_data.responses

    # Group responses by attempt.
    responses_by_attempt: dict[UUID, list[ResponseModel]] = {
        attempt.id: [] for attempt in attempts
    }

    for response in responses:
        responses_by_attempt.setdefault(
            response.attempt_id, []
        ).append(response)

    # Sort by score, highest first. Missing scores rank below scored attempts.
    ranked_attempts = sorted(
        attempts,
        key=lambda attempt: (
            attempt.final_score is not None,
            (
                float(attempt.final_score)
                if attempt.final_score is not None
                else 0.0
            ),
        ),
        reverse=True,
    )

    # Competition ranking: 1, 1, 3 for tied first-place scores.
    ranks: dict[UUID, int] = {}
    previous_score = object()
    current_rank = 0

    for position, attempt in enumerate(ranked_attempts, start=1):
        score = attempt.final_score

        if score != previous_score:
            current_rank = position
            previous_score = score

        ranks[attempt.id] = current_rank

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Exam Results"

    headers = [
        "Name",
        "Enrollment No.",
        "Email address",
        "State",
        "Started on",
        "Completed",
        "Time taken",
        "Total Questions",
        "Questions Attempted",
        "Questions Correct",
        "Questions Incorrect",
        "Percentage",
        "Rank",
        "Grade/20.00",
    ]

    for index, exam_item in enumerate(exam_items, start=1):
        marks = getattr(exam_item, "marks", None)
        marks = float(marks) if marks is not None else 1.0
        headers.append(f"Q. {index} /{marks:.2f}")

    worksheet.append(headers)

    for cell in worksheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(
            fill_type="solid",
            fgColor="1F4E78",
        )

    for attempt in ranked_attempts:
        # AuthClient returns UserSummary objects, not dictionaries.
        student = users_by_id.get(str(attempt.student_id))

        if student:
            student_name = " ".join(
                value
                for value in (
                    student.firstname,
                    student.lastname,
                )
                if value
            ).strip()
            email_address = student.email or ""
        else:
            student_name = ""
            email_address = ""

        if not student_name:
            student_name = str(attempt.student_id)

        attempt_responses = responses_by_attempt.get(
            attempt.id, []
        )

        response_by_exam_item = {
            response.exam_item_id: response
            for response in attempt_responses
            if response.exam_item_id is not None
        }

        # Legacy fallback; safe only if each underlying item appears once
        # in the exam when exam_item_id is absent.
        response_by_item = {
            response.item_id: response
            for response in attempt_responses
        }

        question_results = []
        questions_attempted = 0
        questions_correct = 0
        questions_incorrect = 0

        for exam_item in exam_items:
            response = response_by_exam_item.get(exam_item.id)

            if response is None:
                response = response_by_item.get(exam_item.item_id)

            if response is None or not answer_is_present(
                response.answer
            ):
                question_results.append("-")
                continue

            questions_attempted += 1

            if response.is_correct is True:
                questions_correct += 1
                question_results.append(1)
            elif response.is_correct is False:
                questions_incorrect += 1
                question_results.append(0)
            else:
                # Answer exists but correctness is not yet determined.
                question_results.append("-")

        started_at = attempt.started_at
        submitted_at = attempt.submitted_at

        started_on = (
            started_at.strftime("%Y-%m-%d %H:%M:%S")
            if started_at
            else ""
        )

        completed = (
            submitted_at.strftime("%Y-%m-%d %H:%M:%S")
            if submitted_at
            else ""
        )

        time_taken = ""

        if started_at:
            end_time = submitted_at or datetime.now(
                started_at.tzinfo
            )

            elapsed_seconds = max(
                0,
                int((end_time - started_at).total_seconds()),
            )
            minutes, seconds = divmod(elapsed_seconds, 60)
            time_taken = f"{minutes}m {seconds}s"

        percentage = (
            float(attempt.percentage)
            if attempt.percentage is not None
            else 0.0
        )
        grade = round(percentage / 5, 2)

        worksheet.append(
            [
                student_name,
                "",  # Populate from the actual enrollment-number source.
                email_address,
                "",  # Populate if student state is available.
                started_on,
                completed,
                time_taken,
                len(exam_items),
                questions_attempted,
                questions_correct,
                questions_incorrect,
                percentage,
                ranks.get(attempt.id, ""),
                grade,
                *question_results,
            ]
        )

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    fixed_widths = {
        "A": 28,
        "B": 24,
        "C": 32,
        "D": 16,
        "E": 22,
        "F": 22,
        "G": 16,
        "H": 16,
        "I": 22,
        "J": 20,
        "K": 22,
        "L": 14,
        "M": 10,
        "N": 14,
    }

    for column, width in fixed_widths.items():
        worksheet.column_dimensions[column].width = width

    for column_index in range(15, worksheet.max_column + 1):
        worksheet.column_dimensions[
            get_column_letter(column_index)
        ].width = 14

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    return output
