from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from exam_service.core.redis.redis_client import redis_client
from exam_service.database.database import SessionDep
from exam_service.dependencies import get_current_user, require_roles
from exam_service.schemas.schemas import (
    AssignCohortRequest, ExamCreate, ExamUpdate, ExamRead,
    ExamSectionCreate, ExamSectionRead,
    ExamItemAdd, ExamItemRead,
    ApprovalAction, AssignStudentsRequest,
    ExamAssignmentRead, ExamAuditLogRead, CurrentUser, MyAssignmentRead,
)
from exam_service.services.exam_service import ExamService
from exam_service.database.models.enums import UserRole
from exam_service.clients.auth_client import AuthClient

router = APIRouter(prefix="/exams", tags=["exams"])

AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER)
StudentOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER, UserRole.STUDENT)


''' CREATE EXAM 📝 '''
@router.post("", response_model=ExamRead)
async def create_exam(
    payload: ExamCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    exam = ExamService.create(session, payload, current_user)
    return ExamRead.model_validate(exam, from_attributes=True)


''' LIST EXAMS 📋 '''
@router.get("", response_model=list[ExamRead])
async def list_exams(
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    exams = ExamService.get_all(session, current_user)
    return [ExamRead.model_validate(e, from_attributes=True) for e in exams]


''' MY ASSIGNED EXAMS 🎓 '''
@router.get("/my-assignments", response_model=list[MyAssignmentRead])
async def get_my_assignments(
    session: SessionDep,
    current_user: CurrentUser = Depends(StudentOrAbove),
):
    return ExamService.get_my_assignments(session, current_user)


''' GET EXAM 🔍 '''
@router.get("/{exam_id}", response_model=ExamRead)
async def get_exam(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(StudentOrAbove),
):
    exam = ExamService.get_by_id(session, exam_id, current_user)
    return ExamRead.model_validate(exam, from_attributes=True)


''' UPDATE EXAM ✏️ '''
@router.patch("/{exam_id}", response_model=ExamRead)
async def update_exam(
    exam_id: UUID,
    payload: ExamUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    exam = ExamService.update(session, exam_id, payload, current_user)
    return ExamRead.model_validate(exam, from_attributes=True)


''' DELETE EXAM 🗑️ '''
@router.delete("/{exam_id}", status_code=204)
async def delete_exam(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    ExamService.delete(session, exam_id, current_user)


# --------------------------------------------------------
# APPROVAL FLOW
# --------------------------------------------------------

''' SUBMIT FOR APPROVAL 📤 '''
@router.post("/{exam_id}/submit", response_model=ExamRead)
async def submit_for_approval(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    exam = ExamService.submit_for_approval(session, exam_id, current_user)
    return ExamRead.model_validate(exam, from_attributes=True)


''' APPROVE OR REJECT ✅❌ '''
@router.post("/{exam_id}/approval", response_model=ExamRead)
async def process_approval(
    exam_id: UUID,
    payload: ApprovalAction,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    exam = ExamService.process_approval(session, exam_id, payload, current_user)
    return ExamRead.model_validate(exam, from_attributes=True)


# --------------------------------------------------------
# SECTIONS
# --------------------------------------------------------

''' ADD SECTION 📑 '''
@router.post("/{exam_id}/sections", response_model=ExamSectionRead)
async def add_section(
    exam_id: UUID,
    payload: ExamSectionCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    section = ExamService.add_section(session, exam_id, payload, current_user)
    return ExamSectionRead.model_validate(section, from_attributes=True)


''' LIST SECTIONS 📋 '''
@router.get("/{exam_id}/sections", response_model=list[ExamSectionRead])
async def list_sections(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    sections = ExamService.get_sections(session, exam_id, current_user)
    return [ExamSectionRead.model_validate(s, from_attributes=True) for s in sections]


# --------------------------------------------------------
# ITEMS
# --------------------------------------------------------

''' ADD ITEMS TO EXAM ➕ '''
@router.post("/{exam_id}/items", response_model=list[ExamItemRead])
async def add_items(
    exam_id: UUID,
    payload: ExamItemAdd,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    items = ExamService.add_items(session, exam_id, payload, current_user)
    return [ExamItemRead.model_validate(i, from_attributes=True) for i in items]


''' LIST EXAM ITEMS 📋 '''
@router.get("/{exam_id}/items", response_model=list[ExamItemRead])
async def list_exam_items(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    items = ExamService.get_items(session, exam_id, current_user)
    return [ExamItemRead.model_validate(i, from_attributes=True) for i in items]


''' REMOVE ITEM FROM EXAM ➖ '''
@router.delete("/{exam_id}/items/{exam_item_id}", status_code=204)
async def remove_item(
    exam_id: UUID,
    exam_item_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    ExamService.remove_item(session, exam_id, exam_item_id, current_user)


# --------------------------------------------------------
# STUDENT ASSIGNMENT
# --------------------------------------------------------

''' ASSIGN STUDENTS 🎓 '''
@router.post("/{exam_id}/assign", response_model=list[ExamAssignmentRead])
async def assign_students(
    exam_id: UUID,
    payload: AssignStudentsRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    assignments = ExamService.assign_students(session, exam_id, payload, current_user)
    return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]


''' LIST ASSIGNMENTS 📋 '''
@router.get("/{exam_id}/assignments", response_model=list[ExamAssignmentRead])
async def list_assignments(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    assignments = ExamService.get_assignments(session, exam_id, current_user)
    return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]


# --------------------------------------------------------
# AUDIT LOG
# --------------------------------------------------------

''' AUDIT LOG 📜 '''
@router.get("/{exam_id}/audit", response_model=list[ExamAuditLogRead])
async def get_audit_log(
    exam_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
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
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    """
    Assign all active students in a cohort to this exam.
    Fetches student list from auth service.
    Graduated cohorts cannot be assigned new exams.
    """
    # Fetch student IDs from auth service
    auth_client = AuthClient(redis_client)
    try:
        student_ids = await auth_client.get_cohort_student_ids(
            cohort_id=payload.cohort_id,
            org_id=current_user.org_id,
        )
    except Exception:
        raise HTTPException(
            status_code=502,
            detail="Could not fetch cohort members from auth service."
        )

    if not student_ids:
        raise HTTPException(
            status_code=400,
            detail="Cohort has no active students."
        )

    assignments = ExamService.assign_cohort(
        session=session,
        exam_id=exam_id,
        cohort_id=payload.cohort_id,
        scheduled_at=payload.scheduled_at,
        current_user=current_user,
        student_ids=student_ids,
    )
    return [ExamAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]
