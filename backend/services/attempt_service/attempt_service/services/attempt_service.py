from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select
from pydantic import BaseModel
from typing import Any

from attempt_service.database.models.attempt import AttemptModel, ResponseModel

from attempt_service.schemas.schemas import (
    StartAttemptRequest,
    SaveResponseRequest,
    ManualReviewRequest,
    AttemptRead,
    ResponseRead,
    CurrentUser,
)


# ============================================================
# SERVICE
# ============================================================

class AttemptService:

    @staticmethod
    def start(session: Session, payload: StartAttemptRequest, student_id: UUID, org_id: UUID) -> AttemptModel:
        # Check existing attempts
        existing = session.exec(
            select(AttemptModel).where(
                AttemptModel.exam_id == payload.exam_id,
                AttemptModel.student_id == student_id,
            )
        ).all()

        # NOTE: max_attempts check would call exam service or read from a local
        # cache/snapshot. For now we track attempt_number.
        in_progress = [a for a in existing if a.status == "started"]
        if in_progress:
            raise HTTPException(status_code=400, detail="You already have an attempt in progress.")

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
    def save_response(
        session: Session,
        attempt_id: UUID,
        payload: SaveResponseRequest,
        student_id: UUID,
    ) -> ResponseModel:
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == attempt_id)
        ).first()

        if not attempt or attempt.student_id != student_id:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        if attempt.status != "started":
            raise HTTPException(status_code=400, detail="Cannot save response — attempt is not active.")

        # Upsert — update existing response or create new
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
        item_bank: dict,        # {item_id: {correct_answer, marks, negative_marks, type}}
        pass_mark: float | None = None,
    ) -> AttemptModel:
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == attempt_id)
        ).first()

        if not attempt or attempt.student_id != student_id:
            raise HTTPException(status_code=404, detail="Attempt not found.")

        if attempt.status != "started":
            raise HTTPException(status_code=400, detail="Attempt already submitted.")

        responses = session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()

        raw_score = 0.0
        final_score = 0.0
        total_possible = sum(v.get("marks", 1.0) for v in item_bank.values())

        for resp in responses:
            item_data = item_bank.get(str(resp.item_id))
            if not item_data:
                continue

            item_type = item_data.get("type")
            correct = item_data.get("correct_answer", [])
            marks = item_data.get("marks", 1.0)
            neg = item_data.get("negative_marks", 0.0)

            # Auto-mark objective types
            if item_type in ("mcq_single", "mcq_multi", "true_false", "numeric"):
                student_answer = sorted(resp.answer or [])
                correct_answer = sorted(correct or [])

                if student_answer == correct_answer:
                    resp.is_correct = True
                    resp.marks_awarded = marks
                    raw_score += marks
                    final_score += marks
                elif resp.answer:  # wrong answer (not unanswered)
                    resp.is_correct = False
                    resp.marks_awarded = -neg
                    final_score -= neg
                else:
                    resp.is_correct = False
                    resp.marks_awarded = 0.0
            else:
                # short_answer — leave for manual review
                resp.is_correct = None
                resp.marks_awarded = None

            session.add(resp)

        attempt.status = "submitted"
        attempt.submitted_at = datetime.now(timezone.utc)
        attempt.raw_score = raw_score
        attempt.final_score = max(0.0, final_score)         # floor at 0
        attempt.percentage = round((attempt.final_score / total_possible * 100), 2) if total_possible else 0.0
        attempt.scored_at = datetime.now(timezone.utc)
        attempt.scored_by = "auto"

        if pass_mark is not None:
            attempt.passed = attempt.final_score >= pass_mark

        session.add(attempt)
        session.commit()
        session.refresh(attempt)
        return attempt

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
    def get_attempt(session: Session, attempt_id: UUID, user_id: UUID) -> AttemptModel:
        attempt = session.exec(
            select(AttemptModel).where(AttemptModel.id == attempt_id)
        ).first()
        if not attempt or attempt.student_id != user_id:
            raise HTTPException(status_code=404, detail="Attempt not found.")
        return attempt

    @staticmethod
    def get_responses(session: Session, attempt_id: UUID, user_id: UUID) -> list[ResponseModel]:
        AttemptService.get_attempt(session, attempt_id, user_id)
        return session.exec(
            select(ResponseModel).where(ResponseModel.attempt_id == attempt_id)
        ).all()
