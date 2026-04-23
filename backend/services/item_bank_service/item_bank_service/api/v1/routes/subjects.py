from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from item_bank_service.database.models.subject import SubjectModel
from item_bank_service.clients.auth_client import AuthClient
from item_bank_service.core.redis import redis_client
from item_bank_service.database.database import SessionDep
from item_bank_service.dependencies import get_current_user, require_roles
from item_bank_service.schemas.schemas import (
    SubjectAssignmentEnriched, SubjectCreate, SubjectSummary, SubjectUpdate, SubjectRead,
    SubjectAssignRequest, SubjectAssignmentRead, CurrentUser
)
from item_bank_service.services.subject_service import SubjectService
from item_bank_service.database.models.enums import UserRole
from item_bank_service.core.routes import ItemBankRoutes

router = APIRouter(prefix="/subjects", tags=["subjects"])

AdminOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER)


''' CREATE SUBJECT 📚 '''
@router.post("", response_model=SubjectRead)
async def create_subject(
    payload: SubjectCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    subject = SubjectService.create(session, payload, current_user)
    return SubjectRead.model_validate(subject, from_attributes=True)


''' LIST SUBJECTS 📋 '''
@router.get("", response_model=list[SubjectRead])
async def list_subjects(
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    subjects = SubjectService.get_all(session, current_user)
    return [SubjectRead.model_validate(s, from_attributes=True) for s in subjects]


''' GET SUBJECT BY ID 🔍 '''
@router.get("/{subject_id}", response_model=SubjectRead)
async def get_subject(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
):
    subject = SubjectService.get_by_id(session, subject_id, current_user)
    return SubjectRead.model_validate(subject, from_attributes=True)


''' UPDATE SUBJECT ✏️ '''
@router.patch("/{subject_id}", response_model=SubjectRead)
async def update_subject(
    subject_id: UUID,
    payload: SubjectUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    subject = SubjectService.update(session, subject_id, payload, current_user)
    return SubjectRead.model_validate(subject, from_attributes=True)


''' ARCHIVE SUBJECT 🗄️ '''
@router.delete("/{subject_id}", response_model=SubjectRead)
async def archive_subject(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    subject = SubjectService.archive(session, subject_id, current_user)
    return SubjectRead.model_validate(subject, from_attributes=True)


''' ASSIGN STAFF TO SUBJECT 👥 '''
@router.post("/{subject_id}/assign", response_model=list[SubjectAssignmentRead])
async def assign_subject(
    subject_id: UUID,
    payload: SubjectAssignRequest,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    assignments = SubjectService.assign_users(session, subject_id, payload.user_ids, current_user)
    return [SubjectAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]


''' UNASSIGN STAFF FROM SUBJECT ❌ '''
@router.delete("/{subject_id}/assign/{user_id}", status_code=204)
async def unassign_subject(
    subject_id: UUID,
    user_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    SubjectService.unassign_user(session, subject_id, user_id, current_user)


''' GET SUBJECT ASSIGNMENTS 📋 '''
@router.get("/{subject_id}/assignments", response_model=list[SubjectAssignmentEnriched])
async def get_subject_assignments(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(AdminOrAbove),
):
    assignments = SubjectService.get_assignments(session, subject_id, current_user)

    # Fetch subject from local DB — no HTTP call needed
    subject = session.exec(
        select(SubjectModel).where(SubjectModel.id == subject_id)
    ).first()

    # Collect all user IDs we need — avoids N+1
    user_ids = set()
    for a in assignments:
        user_ids.add(a.assigned_to)
        user_ids.add(a.assigned_by)

    auth_client = AuthClient(redis_client)
    users = await auth_client.get_users_bulk(list(user_ids))

    return [
        SubjectAssignmentEnriched(
            id=a.id,
            subject=SubjectSummary(
                id=subject.id,
                name=subject.name,
                status=subject.status,
            ),
            assigned_to=users.get(str(a.assigned_to)),
            assigned_by=users.get(str(a.assigned_by)),
            created_at=a.created_at,
        )
        for a in assignments
    ]
# @router.get("/{subject_id}/assignments", response_model=list[SubjectAssignmentRead])
# async def get_subject_assignments(
#     subject_id: UUID,
#     session: SessionDep,
#     current_user: CurrentUser = Depends(AdminOrAbove),
# ):
#     assignments = SubjectService.get_assignments(session, subject_id, current_user)
#     return [SubjectAssignmentRead.model_validate(a, from_attributes=True) for a in assignments]
