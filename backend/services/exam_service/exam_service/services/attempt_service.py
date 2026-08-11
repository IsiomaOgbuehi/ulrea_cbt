from uuid import UUID
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import Session, delete, select
from pydantic import BaseModel
from typing import Any
import random

from exam_service.database.models.exam import ExamAssignment, ExamModel
from exam_service.services.exam_service import ExamService, _log
from exam_service.clients.item_bank_client import ItemBankClient
from exam_service.database.models.attempt import AttemptModel, ResponseModel

from exam_service.schemas.attempt_schemas import (
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
    ResponseRead,
    CurrentUser,
)
from exam_service.database.models.attempt_enum import AttemptStatus


# ============================================================
# SERVICE
# ============================================================

class AttemptService:

    # @staticmethod
    # def start(session: Session, payload: StartAttemptRequest, student_id: UUID, org_id: UUID) -> AttemptModel:
    #     # Check existing attempts
    #     existing = session.exec(
    #         select(AttemptModel).where(
    #             AttemptModel.exam_id == payload.exam_id,
    #             AttemptModel.student_id == student_id,
    #         )
    #     ).all()

    #     # NOTE: max_attempts check would call exam service or read from a local
    #     # cache/snapshot. For now we track attempt_number.
    #     in_progress = [a for a in existing if a.status == AttemptStatus.STARTED]
    #     if in_progress:
    #         raise HTTPException(status_code=400, detail="You already have an attempt in progress.")

    #     attempt = AttemptModel(
    #         exam_id=payload.exam_id,
    #         student_id=student_id,
    #         org_id=org_id,
    #         assignment_id=payload.assignment_id,
    #         attempt_number=len(existing) + 1,
    #     )
    #     session.add(attempt)
    #     session.commit()
    #     session.refresh(attempt)
    #     return attempt

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

        # Check existing attempts
        existing = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == payload.exam_id,
                AttemptModel.student_id == student_id,
            )
        ).all()

        # in_progress = [a for a in existing if a.status == AttemptStatus.STARTED]
        in_progress = next((a for a in existing if a.status == AttemptStatus.STARTED), None)
        if in_progress:
            return in_progress
            # raise HTTPException(status_code=400, detail="You already have an attempt in progress.")

        # Enforce max_attempts — same-service call now that exam_service is merged in,
        # no client/network round-trip needed.
        max_attempts = exam.max_attempts or 1  # default to single-attempt if unset
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

    @staticmethod
    async def save_response(
        session: Session,
        attempt_id: UUID,
        payload: SaveResponseRequest,
        student_id: UUID,
    ) -> ResponseModel:
        attempt = await AttemptService.get_attempt(session, attempt_id, student_id)  # expiry-checked

        if attempt.status != AttemptStatus.STARTED:
            raise HTTPException(status_code=400, detail="Cannot save response — attempt is no longer active.")

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
    async def submit(
        session: Session,
        attempt_id: UUID,
        student_id: UUID,
        pass_mark: float | None = None,
    ) -> AttemptModel:
        attempt = await AttemptService.get_attempt(session, attempt_id, student_id)  # expiry-checked

        if attempt.status != AttemptStatus.STARTED:
            raise HTTPException(status_code=400, detail="Attempt already submitted.")

        exam = session.exec(select(ExamModel).where(ExamModel.id == attempt.exam_id)).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        return await AttemptService._finalize_submission(session, attempt, exam, pass_mark=pass_mark, auto=False)
    

    @staticmethod
    def manual_review(
        session: Session,
        response_id: UUID,
        payload: ManualReviewRequest,
        reviewer_id: UUID,
    ) -> ResponseModel:
        resp = session.exec(
            select(ResponseModel).where(ResponseModel.id == response_id)
        ).first()

        if not resp:
            raise HTTPException(status_code=404, detail="Response not found.")

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
    async def get_attempt(session: Session, attempt_id: UUID, user_id: UUID) -> AttemptModel:
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == attempt_id)
        ).first()
        if not attempt or attempt.student_id != user_id:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        return await AttemptService._enforce_time_limit(session, attempt)
        # return attempt

    @staticmethod
    async def get_responses(session: Session, attempt_id: UUID, user_id: UUID) -> list[ResponseModel]:
        await AttemptService.get_attempt(session, attempt_id, user_id)  # expiry-checked
        return session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()
    


    @staticmethod
    async def get_exam_content(session: Session, attempt_id: UUID, student_id: UUID) -> AttemptExamRead:
        attempt = await AttemptService.get_attempt(session, attempt_id, student_id)

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

        item_bank_client = ItemBankClient()
        items_by_id = await item_bank_client.get_items_for_display(all_item_ids)

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


    @staticmethod
    def get_cohort_attempts(
        session: Session,
        exam_id: UUID,
        cohort_id: UUID,
        current_user: CurrentUser,
    ) -> list[CohortAttemptSummary]:
        attempts = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == exam_id,
                AttemptModel.org_id == current_user.org_id,
            )
        ).all()

        # Filter to the cohort via assignment lookup, since AttemptModel stores assignment_id
        assignment_ids = {a.assignment_id for a in attempts}
        cohort_assignments = session.exec(
            select(ExamAssignment.id).where(
                ExamAssignment.id.in_(assignment_ids),
                ExamAssignment.cohort_id == cohort_id,
            )
        ).all()
        cohort_assignment_ids = set(cohort_assignments)

        return [
            CohortAttemptSummary(
                id=a.id, student_id=a.student_id, status=a.status,
                submitted_at=a.submitted_at, final_score=a.final_score,
                percentage=a.percentage, passed=a.passed,
            )
            for a in attempts if a.assignment_id in cohort_assignment_ids
        ]


    @staticmethod
    async def get_attempt_detail_for_staff(
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

        responses = session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()

        item_ids = [r.item_id for r in responses]
        item_bank_client = ItemBankClient()
        item_bank = await item_bank_client.get_items_for_scoring(item_ids)  # teacher view — answer keys OK here

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
        attempt = session.exec(
            select(AttemptModel).where(
                AttemptModel.id == attempt_id,
                AttemptModel.org_id == current_user.org_id,
            )
        ).first()
        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        # Wipe responses so the student gets a clean slate
        session.exec(
            delete(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        )

        attempt.status = AttemptStatus.RESET
        attempt.submitted_at = None
        attempt.raw_score = None
        attempt.final_score = None
        attempt.percentage = None
        attempt.passed = None
        attempt.scored_at = None
        attempt.scored_by = None
        session.add(attempt)

        if payload.new_scheduled_at:
            assignment = session.exec(
                select(ExamAssignment).where(ExamAssignment.id == attempt.assignment_id)
            ).first()
            if assignment:
                assignment.scheduled_at = payload.new_scheduled_at
                session.add(assignment)

        _log(session, attempt.exam_id, current_user.org_id, current_user.id,
            "attempt_reset", {"attempt_id": str(attempt_id), "reason": payload.reason})

        session.commit()
        session.refresh(attempt)
        return attempt





    @staticmethod
    async def _finalize_submission(
        session: Session,
        attempt: AttemptModel,
        exam: ExamModel,
        pass_mark: float | None = None,
        auto: bool = False,
    ) -> AttemptModel:
        """
        Scores and closes out an attempt. Shared by student-triggered submit
        and lazy time-expiry auto-submit, so both paths score identically.
        """
        responses = session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt.id)
        ).all()

        item_ids = [r.item_id for r in responses]
        item_bank_client = ItemBankClient()
        item_bank = await item_bank_client.get_items_for_scoring(item_ids)  # dict[UUID, dict]

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
                student_answer = sorted(resp.answer or [])
                correct_answer = sorted(correct or [])

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
                resp.marks_awarded = None  # short_answer/essay — awaits manual review

            session.add(resp)

        attempt.status = AttemptStatus.SUBMITTED
        attempt.submitted_at = datetime.now(timezone.utc)
        attempt.raw_score = raw_score
        attempt.final_score = max(0.0, final_score)
        attempt.percentage = round((attempt.final_score / total_possible * 100), 2) if total_possible else 0.0
        attempt.scored_at = datetime.now(timezone.utc)
        attempt.scored_by = "auto_expired" if auto else "auto"

        effective_pass_mark = pass_mark if pass_mark is not None else exam.pass_mark
        if effective_pass_mark is not None:
            attempt.passed = attempt.final_score >= effective_pass_mark

        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        return attempt



    @staticmethod
    async def _enforce_time_limit(session: Session, attempt: AttemptModel) -> AttemptModel:
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

        return await AttemptService._finalize_submission(session, attempt, exam, auto=True)
