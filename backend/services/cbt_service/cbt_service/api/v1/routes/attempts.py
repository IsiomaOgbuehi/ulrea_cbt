from uuid import UUID
from fastapi import APIRouter, Depends
from cbt_service.database.database import SessionDep
from cbt_service.dependencies import get_current_user
from cbt_service.schemas.attempt_schemas import (
    AttemptDetailRead,
    AttemptExamRead,
    CohortAttemptSummary,
    ResetAttemptRequest,
    StartAttemptRequest,
    SaveResponseRequest,
    ManualReviewRequest,
    AttemptRead,
    ResponseRead,
    CurrentUser,
)
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.dependencies import require_roles
from cbt_service.services.attempt_service import AttemptService

router = APIRouter(prefix="/attempts", tags=["attempts"])

AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER, UserRole.STAFF)


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
    # return AttemptRead.model_validate(attempt, from_attributes=True)
    return AttemptService._to_attempt_read(session, attempt)


''' GET ATTEMPT 🔍 '''
@router.get("/{attempt_id}", response_model=AttemptRead)
async def get_attempt(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    attempt = await AttemptService.get_attempt(session, attempt_id, current_user.id)
    # return AttemptRead.model_validate(attempt, from_attributes=True)
    return AttemptService._to_attempt_read(session, attempt)


''' SAVE RESPONSE 💾 '''
@router.post("/{attempt_id}/responses", response_model=ResponseRead)
async def save_response(
    attempt_id: UUID,
    payload: SaveResponseRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Autosave a student's answer. Call on every answer change."""
    response = await AttemptService.save_response(
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
    responses = await AttemptService.get_responses(session, attempt_id, current_user.id)
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
    
    attempt = await AttemptService.submit(
        session=session,
        attempt_id=attempt_id,
        student_id=current_user.id,
        pass_mark=pass_mark,
    )
    # return AttemptRead.model_validate(attempt, from_attributes=True)
    return AttemptService._to_attempt_read(session, attempt)


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




''' GET EXAM CONTENT FOR ATTEMPT 📄 '''
@router.get("/{attempt_id}/exam", response_model=AttemptExamRead)
async def get_attempt_exam(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Returns exam structure for an in-progress attempt. No answer keys."""
    return await AttemptService.get_exam_content(session, attempt_id, current_user.id)




''' COHORT ATTEMPTS 📊 '''
@router.get("/exams/{exam_id}/cohorts/{cohort_id}/attempts", response_model=list[CohortAttemptSummary])
async def get_cohort_attempts(
    exam_id: UUID,
    cohort_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    return AttemptService.get_cohort_attempts(session, exam_id, cohort_id, current_user)


''' ATTEMPT DETAIL (STAFF VIEW) 🔍 '''
@router.get("/{attempt_id}/detail", response_model=AttemptDetailRead)
async def get_attempt_detail(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    return await AttemptService.get_attempt_detail_for_staff(session, attempt_id, current_user)



''' RESET/RESCHEDULE ATTEMPT 🔄 '''
@router.post("/{attempt_id}/reset", response_model=AttemptRead)
async def reset_attempt(
    attempt_id: UUID,
    payload: ResetAttemptRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    attempt = AttemptService.reset_attempt(session, attempt_id, payload, current_user)
    return AttemptService._to_attempt_read(session, attempt)