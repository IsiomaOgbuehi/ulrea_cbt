from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from cbt_service.database.models.enums.exam_enum import ExamStatus
from cbt_service.database.models.exam import ExamAssignment
from cbt_service.schemas.item_subject_schemas import ExamItemDetailRead, ItemRead, PaginatedResponse
from cbt_service.core.redis.redis_client import redis_client
from cbt_service.database.database import SessionDep, release_connection
from cbt_service.dependencies import get_current_user
from cbt_service.schemas.exam_schemas import (
    AssignCohortRequest, ExamCreate, ExamDeleteResponse, ExamUpdate, ExamRead,
    ExamSectionCreate, ExamSectionRead,
    ExamItemAdd, ExamItemRead,
    ApprovalAction, AssignStudentsRequest,
    ExamAssignmentRead, ExamAuditLogRead, CurrentUser, MyAssignmentRead, ExamStatusUpdate, UserSummary
)
from cbt_service.services.exam_service import ExamService
from cbt_service.schemas.bank_topic_schemas import ExamItemsFromBank, ExamItemsFromBankResult
from cbt_service.schemas.exam_schemas import ExamPhase, ExamPhaseCounts
from cbt_service.clients.auth_client import AuthClient
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(prefix="/exams", tags=["exams"])


# new


''' CREATE EXAM 📝 '''
@router.post("", response_model=ExamRead)
def create_exam(
    payload: ExamCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_CREATE)),
):
    exam = ExamService.create(session, payload, current_user)
    return ExamService.to_reads(session, [exam])[0]


# ''' LIST EXAMS 📋 '''
# @router.get("", response_model=list[ExamRead])
# def list_exams(
#     session: SessionDep,
#     current_user: CurrentUser = Depends(StaffViewer),
# ):
#     rows = ExamService.get_all(session, current_user)
#     return [
#         ExamRead.model_validate({**exam.model_dump(), "subject_name": subject_name})
#         for exam, subject_name in rows
#     ]

''' LIST EXAMS 📋 '''
@router.get("", response_model=PaginatedResponse[ExamRead])
def list_exams(
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_VIEW)),
    status: ExamStatus | None = Query(default=None),
    subject_id: UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100, description="Matches exam title"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    period_id: UUID | None = Query(default=None, description="Only exams for this period (a session includes its terms)"),
    phase: ExamPhase | None = Query(default=None, description="upcoming | live | past | drafts (from status and window)"),
) -> PaginatedResponse[ExamRead]:
    rows, total = ExamService.get_all(
        session, current_user,
        status=status, subject_id=subject_id, search=search,
        page=page, page_size=page_size, period_id=period_id, phase=phase,
    )
    return PaginatedResponse(
        items=ExamService.to_reads(
            session,
            [exam for exam, _ in rows],
            {exam.id: subject_name for exam, subject_name in rows},
        ),
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


''' EXAM COUNTS BY PHASE 📊 '''
@router.get("/summary", response_model=ExamPhaseCounts)
def exam_phase_counts(
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_VIEW)),
    subject_id: UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=100),
    period_id: UUID | None = Query(default=None),
) -> ExamPhaseCounts:
    """Counts for the exams list tabs, using the same filters as the list."""
    return ExamService.phase_counts(
        session, current_user, subject_id=subject_id, search=search, period_id=period_id
    )


''' MY ASSIGNED EXAMS 🎓 '''
@router.get("/my-assignments", response_model=list[MyAssignmentRead])
def get_my_assignments(
    session: SessionDep,
    current_user: CurrentUser = Depends(require_candidate_or(P.EXAMS_VIEW)),
):
    return ExamService.get_my_assignments(session, current_user)


''' GET EXAM 🔍 '''
@router.get("/{exam_id}", response_model=ExamRead)
def get_exam(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_candidate_or(P.EXAMS_VIEW)),
):
    exam = ExamService.get_by_id(session, exam_id, current_user)
    return ExamService.to_reads(session, [exam])[0]


''' UPDATE EXAM ✏️ '''
@router.patch("/{exam_id}", response_model=ExamRead)
def update_exam(
    exam_id: UUID,
    payload: ExamUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
):
    exam = ExamService.update(session, exam_id, payload, current_user)
    return ExamService.to_reads(session, [exam])[0]


''' DELETE EXAM 🗑️ '''
@router.delete("/{exam_id}", response_model=ExamDeleteResponse)
def delete_exam(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_DELETE)),
):
    return ExamService.delete(session, exam_id, current_user)


# --------------------------------------------------------
# APPROVAL FLOW
# --------------------------------------------------------

''' SUBMIT FOR APPROVAL 📤 '''
@router.post("/{exam_id}/submit", response_model=ExamRead)
def submit_for_approval(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_SUBMIT)),
):
    exam = ExamService.submit_for_approval(session, exam_id, current_user)
    return ExamService.to_reads(session, [exam])[0]


''' APPROVE OR REJECT ✅❌ '''
@router.post("/{exam_id}/approval", response_model=ExamRead)
def process_approval(
    exam_id: UUID,
    payload: ApprovalAction,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_APPROVE)),
):
    exam = ExamService.process_approval(session, exam_id, payload, current_user)
    return ExamService.to_reads(session, [exam])[0]


# --------------------------------------------------------
# SECTIONS
# --------------------------------------------------------

''' ADD SECTION 📑 '''
@router.post("/{exam_id}/sections", response_model=ExamSectionRead)
def add_section(
    exam_id: UUID,
    payload: ExamSectionCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
):
    section = ExamService.add_section(session, exam_id, payload, current_user)
    return ExamSectionRead.model_validate(section, from_attributes=True)


''' LIST SECTIONS 📋 '''
@router.get("/{exam_id}/sections", response_model=list[ExamSectionRead])
def list_sections(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_VIEW)),
):
    sections = ExamService.get_sections(session, exam_id, current_user)
    return [ExamSectionRead.model_validate(s, from_attributes=True) for s in sections]


# --------------------------------------------------------
# ITEMS
# --------------------------------------------------------

''' ADD ITEMS TO EXAM ➕ '''
@router.post("/{exam_id}/items", response_model=list[ExamItemRead])
def add_items(
    exam_id: UUID,
    payload: ExamItemAdd,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
):
    items = ExamService.add_items(session, exam_id, payload, current_user)
    return [ExamItemRead.model_validate(i, from_attributes=True) for i in items]


''' ADD ITEMS BY BANK / TOPIC 🎯 '''
@router.post("/{exam_id}/items/from-bank", response_model=ExamItemsFromBankResult)
def add_items_from_bank(
    exam_id: UUID,
    payload: ExamItemsFromBank,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
) -> ExamItemsFromBankResult:
    """
    Add questions from the exam's subject by bank, topics and/or period, e.g.
    "the whole SS2 bank" or "20 random Algebra questions from SS2, First Term".
    """
    return ExamService.add_items_from_bank(session, exam_id, payload, current_user)


''' LIST EXAM ITEMS 📋 '''
@router.get("/{exam_id}/items", response_model=list[ExamItemRead])
def list_exam_items(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_VIEW)),
):
    items = ExamService.get_items(session, exam_id, current_user)
    return [ExamItemRead.model_validate(i, from_attributes=True) for i in items]


''' REMOVE ITEM FROM EXAM ➖ '''
@router.delete("/{exam_id}/items/{exam_item_id}", status_code=204)
def remove_item(
    exam_id: UUID,
    exam_item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_EDIT)),
):
    ExamService.remove_item(session, exam_id, exam_item_id, current_user)


# --------------------------------------------------------
# STUDENT ASSIGNMENT
# --------------------------------------------------------

# ''' ASSIGN STUDENTS 🎓 '''
# @router.post("/{exam_id}/assign", response_model=list[ExamAssignmentRead])
# def assign_students(
#     exam_id: UUID,
#     payload: AssignStudentsRequest,
#     session: SessionDep,
#     current_user: CurrentUser = Depends(TeacherOrAbove),
# ):
#     assignments = ExamService.assign_students(session, exam_id, payload, current_user)
#     return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]

''' ASSIGN STUDENTS 🎓 '''
@router.post("/{exam_id}/assign", response_model=list[ExamAssignmentRead])
async def assign_students(
    exam_id: UUID,
    payload: AssignStudentsRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_ASSIGN)),
) -> list[ExamAssignmentRead]:
    # 1. Cheap DB check first: 404 / 400 return immediately, no network call.
    await run_in_threadpool(ExamService.assert_assignable, session, exam_id, current_user)
    # Free the connection while we wait on the auth service; step 3 checks out a new one.
    await release_connection(session)

    # 2. Only now ask the auth service whether the IDs are valid students.
    unique_ids: list[UUID] = list(dict.fromkeys(payload.student_ids))

    auth_client = AuthClient(redis_client)
    try:
        valid_ids: set[UUID] = set(
            await auth_client.filter_student_ids(unique_ids, current_user.org_id)
        )
    except Exception:
        raise HTTPException(status_code=502, detail="Could not verify students with auth service.")

    invalid: list[str] = [str(s) for s in unique_ids if s not in valid_ids]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"message": "Some IDs are not active students in your organisation.",
                    "invalid_student_ids": invalid},
        )

    assignments: list[ExamAssignment] = await run_in_threadpool(
        ExamService.assign_students,
        session=session, exam_id=exam_id, payload=payload, current_user=current_user,
    )
    return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]


''' LIST ASSIGNMENTS 📋 '''
@router.get("/{exam_id}/assignments", response_model=list[ExamAssignmentRead])
async def list_assignments(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_ASSIGN)),
):
    assignments: list[ExamAssignmentRead] = await run_in_threadpool(
    ExamService.get_assignments, session, exam_id, current_user)
    # DB work done: free the connection before the auth-service call.
    await release_connection(session)

    if not assignments:
            return assignments

    auth_client = AuthClient(redis_client)
    users: dict[str, UserSummary] = await auth_client.get_users_bulk(
        [s.student_id for s in assignments],
        org_id=current_user.org_id,
    )

    enriched: list[ExamAssignmentRead] = []
    for assignment in assignments:
        user: UserSummary | None = users.get(str(assignment.student_id))
        name: str | None = f"{user.firstname} {user.lastname}".strip() if user else None
        enriched.append(assignment.model_copy(update={"student_name": name}))

    return enriched


# --------------------------------------------------------
# AUDIT LOG
# --------------------------------------------------------

''' AUDIT LOG 📜 '''
@router.get("/{exam_id}/audit", response_model=list[ExamAuditLogRead])
def get_audit_log(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_AUDIT_LOG)),
):
    logs = ExamService.get_audit_log(session, exam_id, current_user)
    return [ExamAuditLogRead.model_validate(l, from_attributes=True) for l in logs]


# --------------------------------------------------------
# ASSIGN COHORT TO EXAM
# --------------------------------------------------------

''' ASSIGN COHORT TO EXAM 🎓 '''
@router.post("/{exam_id}/assign_cohort", response_model=list[ExamAssignmentRead])
async def assign_cohort(
    exam_id: UUID,
    payload: AssignCohortRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_ASSIGN)),
):
    """
    Assign all active students in a cohort to this exam.
    Fetches student list from auth service.
    Graduated cohorts cannot be assigned new exams.
    """
    # 1. Cheap DB checks first: 404 / 403 / 400 return before any network call.
    await run_in_threadpool(ExamService.assert_assignable, session, exam_id, current_user)
    # Free the connection while we wait on the auth service; step 3 checks out a new one.
    await release_connection(session)

    # 2. Fetch the cohort's student IDs from the auth service.
    auth_client = AuthClient(redis_client)
    try:
        student_ids: list[UUID] = await auth_client.get_cohort_student_ids(
            cohort_id=payload.cohort_id, org_id=current_user.org_id,
        )
    except HTTPException:
        raise   # 400 graduated / 404 not found / 502 from the client: pass through
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch cohort members from auth service.")

    if not student_ids:
        raise HTTPException(status_code=400, detail="Cohort has no active students.")

    # 3. Do the assignment.
    assignments = await run_in_threadpool(
    ExamService.assign_cohort,
        session=session,
        exam_id=exam_id,
        cohort_id=payload.cohort_id,
        scheduled_at=payload.scheduled_at,
        current_user=current_user,
        student_ids=student_ids,
    )
    return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]


''' SUPER ADMIN: OVERRIDE EXAM STATUS 🔧 '''
@router.patch("/{exam_id}/status-override", response_model=ExamRead)
def override_exam_status(
    exam_id: UUID,
    payload: ExamStatusUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_OVERRIDE_STATUS)),
):
    """
    Manually force an exam's status. Super admin only — bypasses the normal
    submit/approve/reject workflow, intended for correcting mistakes.
    """
    exam = ExamService.update_status(
        session=session,
        exam_id=exam_id,
        new_status=payload.status,
        reason=payload.reason,
        current_user=current_user,
    )
    return ExamService.to_reads(session, [exam])[0]



''' LIST EXAM QUESTIONS WITH CONTENT 🧐 '''
@router.get("/{exam_id}/questions", response_model=list[ExamItemDetailRead])
def list_exam_questions(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.EXAMS_VIEW)),
):
    rows = ExamService.get_items_with_content(session, exam_id, current_user)
    return [
        ExamItemDetailRead(
            **ExamItemRead.model_validate(exam_item, from_attributes=True).model_dump(),
            item=ItemRead.model_validate(item, from_attributes=True),
        )
        for exam_item, item in rows
    ]