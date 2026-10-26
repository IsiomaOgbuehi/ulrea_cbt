from uuid import UUID
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from cbt_service.schemas.exam_schemas import UserSummary
from cbt_service.core.redis.redis_client import redis_client
from cbt_service.clients.auth_client import AuthClient
from cbt_service.database.models.exam import ExamModel
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.dependencies import get_current_user
from cbt_service.schemas.attempt_schemas import (
    AttemptDetailRead,
    AttemptExamRead,
    AttemptStatusRead,
    CohortAttemptSummary,
    ResetAttemptRequest,
    StartAttemptRequest,
    SaveResponseRequest,
    ManualReviewRequest,
    AttemptRead,
    ResponseRead,
    CurrentUser,
)
from cbt_service.services.attempt_service import AttemptService, build_exam_attempts_workbook
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(prefix="/attempts", tags=["attempts"])



''' START ATTEMPT 🚀 '''
@router.post("", response_model=AttemptRead)
def start_attempt(
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
def get_attempt(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    attempt = AttemptService.get_attempt(session, attempt_id, current_user.id)
    # return AttemptRead.model_validate(attempt, from_attributes=True)
    return AttemptService._to_attempt_read(session, attempt)


''' SAVE RESPONSE 💾 '''
@router.post("/{attempt_id}/responses", response_model=ResponseRead)
def save_response(
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
def get_responses(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    responses = AttemptService.get_responses(session, attempt_id, current_user.id)
    return [ResponseRead.model_validate(r, from_attributes=True) for r in responses]


''' SUBMIT ATTEMPT ✅ '''
@router.post("/{attempt_id}/submit", response_model=AttemptRead)
def submit_attempt(
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
        pass_mark=pass_mark,
    )
    # return AttemptRead.model_validate(attempt, from_attributes=True)
    return AttemptService._to_attempt_read(session, attempt)


''' MANUAL REVIEW ✍️ '''
@router.post("/responses/{response_id}/review", response_model=ResponseRead)
def manual_review(
    response_id: UUID,
    payload: ManualReviewRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_MARK)),
):
    """Teacher or admin manually marks a short answer response."""
    response = AttemptService.manual_review(
        session=session,
        response_id=response_id,
        payload=payload,
        reviewer_id=current_user.id,
        current_user=current_user,
    )
    return ResponseRead.model_validate(response, from_attributes=True)




''' GET EXAM CONTENT FOR ATTEMPT 📄 '''
@router.get("/{attempt_id}/exam", response_model=AttemptExamRead)
def get_attempt_exam(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Returns exam structure for an in-progress attempt. No answer keys."""
    return AttemptService.get_exam_content(session, attempt_id, current_user.id)




# ''' COHORT ATTEMPTS 📊 '''
# @router.get("/exams/{exam_id}/cohorts/{cohort_id}/attempts", response_model=list[CohortAttemptSummary])
# def get_cohort_attempts(
#     exam_id: UUID,
#     cohort_id: UUID,
#     session: SessionDep,
#     current_user: CurrentUser = Depends(TeacherOrAbove),
# ):
#     return AttemptService.get_cohort_attempts(session, exam_id, cohort_id, current_user)

''' COHORT ATTEMPTS 📊 '''
@router.get("/exams/{exam_id}/cohorts/{cohort_id}/attempts", response_model=list[CohortAttemptSummary])
async def get_cohort_attempts(
    exam_id: UUID,
    cohort_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_VIEW)),
) -> list[CohortAttemptSummary]:
    summaries: list[CohortAttemptSummary] = await run_in_threadpool(
        AttemptService.get_cohort_attempts, session, exam_id, cohort_id, current_user,
    )
    # DB work done: free the connection before the auth-service call.
    await release_connection(session)
    if not summaries:
        return summaries

    auth_client = AuthClient(redis_client)
    users: dict[str, UserSummary] = await auth_client.get_users_bulk(
        [s.student_id for s in summaries],
        org_id=current_user.org_id,
    )

    enriched: list[CohortAttemptSummary] = []
    for s in summaries:
        user: UserSummary | None = users.get(str(s.student_id))
        name: str | None = f"{user.firstname} {user.lastname}".strip() if user else None
        enriched.append(s.model_copy(update={"student_name": name}))
    return enriched


''' ATTEMPT DETAIL (STAFF VIEW) 🔍 '''
@router.get("/{attempt_id}/detail", response_model=AttemptDetailRead)
def get_attempt_detail(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.RESULTS_VIEW)),
):
    return AttemptService.get_attempt_detail_for_staff(session, attempt_id, current_user)



''' RESET/RESCHEDULE ATTEMPT 🔄 '''
@router.post("/{attempt_id}/reset", response_model=AttemptRead)
def reset_attempt(
    attempt_id: UUID,
    payload: ResetAttemptRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.ATTEMPTS_RESET)),
) -> AttemptRead:
    attempt = AttemptService.reset_attempt(session, attempt_id, payload, current_user)
    exam = session.get(ExamModel, attempt.exam_id)
    return AttemptService._to_attempt_read_new(attempt, exam)



''' ATTEMPT STATUS (HEARTBEAT) 💓 '''
@router.get("/{attempt_id}/status", response_model=AttemptStatusRead)
def get_attempt_status(
    attempt_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_candidate_or(P.RESULTS_VIEW)),
) -> AttemptStatusRead:
    """Lightweight poll: status, time left, and violation count. Enforces expiry."""
    # Single query; checks ownership and auto-submits if time has run out.
    return AttemptService.get_attempt_status(session, attempt_id, current_user.id)



@router.get("/{exam_id}/result/export")
async def export_exam_attempts_to_excel(
    exam_id: UUID,
    session: SessionDep,
    ctx: CurrentUser = Depends(require_permission(P.RESULTS_EXPORT)),
):
    """Download all attempts for an exam as an Excel workbook."""

    org_id = ctx.org_id

    # Load data with synchronous database operations.
    export_data = await run_in_threadpool(
        AttemptService.load_exam_export_data,
        session,
        exam_id,
        org_id,
        ctx,
    )
    # DB work done: free the connection before the auth-service call
    # and the workbook build.
    await release_connection(session)

    # Fetch student profiles in one request to the auth service.
    auth_client = AuthClient(redis_client)
    
    users_by_id = await auth_client.get_users_bulk(
        user_ids=[
            attempt.student_id
            for attempt in export_data.attempts
        ],
        org_id=org_id,
    )

    # Workbook generation can also run outside the event loop.
    output = await run_in_threadpool(
        build_exam_attempts_workbook,
        export_data,
        users_by_id,
    )

    filename = f"exam-{exam_id}-attempts.xlsx"

    return StreamingResponse(
        output,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
        },
    )