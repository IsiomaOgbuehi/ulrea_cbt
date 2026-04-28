from uuid import UUID
from fastapi import APIRouter, Depends
from attempt_service.database.database import SessionDep
from attempt_service.dependencies import get_current_user
from attempt_service.schemas.schemas import (
    StartAttemptRequest,
    SaveResponseRequest,
    ManualReviewRequest,
    AttemptRead,
    ResponseRead,
    CurrentUser,
)
from attempt_service.database.models.enums import UserRole
from attempt_service.dependencies import require_roles
from attempt_service.services.attempt_service import AttemptService

router = APIRouter(prefix="/attempts", tags=["attempts"])

AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER)


''' START ATTEMPT 🚀 '''
@router.post("", response_model=AttemptRead)
async def start_attempt(
    payload: StartAttemptRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Student starts an exam attempt."""
    attempt = AttemptService.start(
        session=session,
        payload=payload,
        student_id=current_user.id,
        org_id=current_user.org_id,
    )
    return AttemptRead.model_validate(attempt, from_attributes=True)


''' GET ATTEMPT 🔍 '''
@router.get("/{attempt_id}", response_model=AttemptRead)
async def get_attempt(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    attempt = AttemptService.get_attempt(session, attempt_id, current_user.id)
    return AttemptRead.model_validate(attempt, from_attributes=True)


''' SAVE RESPONSE 💾 '''
@router.post("/{attempt_id}/responses", response_model=ResponseRead)
async def save_response(
    attempt_id: UUID,
    payload: SaveResponseRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Autosave a student's answer. Call on every answer change."""
    response = AttemptService.save_response(
        session=session,
        attempt_id=attempt_id,
        payload=payload,
        student_id=current_user.id,
    )
    return ResponseRead.model_validate(response, from_attributes=True)


''' GET RESPONSES 📋 '''
@router.get("/{attempt_id}/responses", response_model=list[ResponseRead])
async def get_responses(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    responses = AttemptService.get_responses(session, attempt_id, current_user.id)
    return [ResponseRead.model_validate(r, from_attributes=True) for r in responses]


''' SUBMIT ATTEMPT ✅ '''
@router.post("/{attempt_id}/submit", response_model=AttemptRead)
async def submit_attempt(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
    pass_mark: float | None = None,
):
    """
    Submit the attempt and trigger auto-scoring.
    item_bank dict would be fetched from item bank service in production.
    For now accepts it as empty — extend with AuthClient pattern.
    """
    attempt = AttemptService.submit(
        session=session,
        attempt_id=attempt_id,
        student_id=current_user.id,
        item_bank={},       # TODO: fetch from item bank service
        pass_mark=pass_mark,
    )
    return AttemptRead.model_validate(attempt, from_attributes=True)


''' MANUAL REVIEW ✍️ '''
@router.post("/responses/{response_id}/review", response_model=ResponseRead)
async def manual_review(
    response_id: UUID,
    payload: ManualReviewRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    """Teacher or admin manually marks a short answer response."""
    response = AttemptService.manual_review(
        session=session,
        response_id=response_id,
        payload=payload,
        reviewer_id=current_user.id,
    )
    return ResponseRead.model_validate(response, from_attributes=True)
