from uuid import UUID

from fastapi import APIRouter, Depends, Query

from cbt_service.database.database import SessionDep
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.dependencies import require_roles
from cbt_service.schemas.bank_topic_schemas import (
    BankCreate, BankRead, BankUpdate, TopicCreate, TopicRead, TopicUpdate,
)
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.services.item.bank_topic_service import BankService, TopicService

router = APIRouter(prefix="/subjects/{subject_id}", tags=["banks & topics"])

# Same roles as items: anyone who can author questions in the subject.
TeacherOrAbove = require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER)


# ------------------------------------------------------------------ banks

''' LIST BANKS 📚 '''
@router.get("/banks", response_model=list[BankRead])
def list_banks(
    subject_id: UUID,
    session: SessionDep,
    include_archived: bool = Query(default=False),
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> list[BankRead]:
    """Banks in the subject (classes/levels, past papers...) with active question counts."""
    return BankService.list(session, subject_id, current_user, include_archived)


''' CREATE BANK ➕ '''
@router.post("/banks", response_model=BankRead, status_code=201)
def create_bank(
    subject_id: UUID,
    payload: BankCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> BankRead:
    return BankService.create(session, subject_id, payload, current_user)


''' UPDATE BANK ✏️ '''
@router.patch("/banks/{bank_id}", response_model=BankRead)
def update_bank(
    subject_id: UUID,
    bank_id: UUID,
    payload: BankUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> BankRead:
    """Rename, reorder, or restore an archived bank (status=active)."""
    return BankService.update(session, subject_id, bank_id, payload, current_user)


''' ARCHIVE BANK 🗄️ '''
@router.delete("/banks/{bank_id}", response_model=BankRead)
def archive_bank(
    subject_id: UUID,
    bank_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> BankRead:
    """Archives the bank. Its questions keep their bank and stay usable."""
    return BankService.archive(session, subject_id, bank_id, current_user)


# ----------------------------------------------------------------- topics

''' LIST TOPICS 🏷️ '''
@router.get("/topics", response_model=list[TopicRead])
def list_topics(
    subject_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> list[TopicRead]:
    """All topics in the subject as a flat list; nest them using parent_id."""
    return TopicService.list(session, subject_id, current_user)


''' CREATE TOPIC ➕ '''
@router.post("/topics", response_model=TopicRead, status_code=201)
def create_topic(
    subject_id: UUID,
    payload: TopicCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> TopicRead:
    return TopicService.create(session, subject_id, payload, current_user)


''' UPDATE TOPIC ✏️ '''
@router.patch("/topics/{topic_id}", response_model=TopicRead)
def update_topic(
    subject_id: UUID,
    topic_id: UUID,
    payload: TopicUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> TopicRead:
    """Rename, reorder or move a topic (parent_id null = top level)."""
    return TopicService.update(session, subject_id, topic_id, payload, current_user)


''' DELETE TOPIC ❌ '''
@router.delete("/topics/{topic_id}", status_code=204)
def delete_topic(
    subject_id: UUID,
    topic_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(TeacherOrAbove),
) -> None:
    """Deletes a topic with no sub-topics; its questions and exams are untagged."""
    TopicService.delete(session, subject_id, topic_id, current_user)
