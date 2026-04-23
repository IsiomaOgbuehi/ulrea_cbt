from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select

from item_bank_service.database.models.item import ItemModel
from item_bank_service.database.models.subject import SubjectModel, SubjectAssignment
from item_bank_service.schemas.schemas import ItemCreate, ItemUpdate, CurrentUser
from item_bank_service.database.models.enums import UserRole


class ItemService:

    @staticmethod
    def _assert_subject_access(session: Session, subject_id: UUID, current_user: CurrentUser):
        """Shared guard — ensures subject exists in org and user has access."""
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,
            )
        ).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        if current_user.role == UserRole.TEACHER:
            assignment = session.exec(
                select(SubjectAssignment).where(
                    SubjectAssignment.subject_id == subject_id,
                    SubjectAssignment.assigned_to == current_user.id,
                )
            ).first()
            if not assignment:
                raise HTTPException(status_code=403, detail="You are not assigned to this subject.")

        return subject

    @staticmethod
    def create(
        session: Session,
        subject_id: UUID,
        payload: ItemCreate,
        current_user: CurrentUser,
    ) -> ItemModel:
        ItemService._assert_subject_access(session, subject_id, current_user)

        item = ItemModel(
            org_id=current_user.org_id,
            subject_id=subject_id,
            created_by=current_user.id,
            source="manual",
            stem=payload.stem,
            type=payload.type,
            options=payload.options,
            correct_answer=payload.correct_answer,
            explanation=payload.explanation,
            marks=payload.marks,
            negative_marks=payload.negative_marks,
            tags=payload.tags,
            difficulty=payload.difficulty,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def get_all(
        session: Session,
        subject_id: UUID,
        current_user: CurrentUser,
        status: str | None = None,
        difficulty: str | None = None,
        item_type: str | None = None,
        search: str | None = None,
    ) -> list[ItemModel]:
        ItemService._assert_subject_access(session, subject_id, current_user)

        query = select(ItemModel).where(
            ItemModel.subject_id == subject_id,
            ItemModel.org_id == current_user.org_id,
        )

        if status:
            query = query.where(ItemModel.status == status)
        if difficulty:
            query = query.where(ItemModel.difficulty == difficulty)
        if item_type:
            query = query.where(ItemModel.type == item_type)
        if search:
            query = query.where(ItemModel.stem.ilike(f"%{search}%"))

        return session.exec(query).all()

    @staticmethod
    def get_by_id(
        session: Session,
        item_id: UUID,
        current_user: CurrentUser,
    ) -> ItemModel:
        item = session.exec(
            select(ItemModel).where(
                ItemModel.id == item_id,
                ItemModel.org_id == current_user.org_id,  # tenant guard
            )
        ).first()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found.")

        # Check subject access
        ItemService._assert_subject_access(session, item.subject_id, current_user)
        return item

    @staticmethod
    def update(
        session: Session,
        item_id: UUID,
        payload: ItemUpdate,
        current_user: CurrentUser,
    ) -> ItemModel:
        item = ItemService.get_by_id(session, item_id, current_user)

        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(item, key, value)

        item.version += 1
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def delete(
        session: Session,
        item_id: UUID,
        current_user: CurrentUser,
    ) -> None:
        item = ItemService.get_by_id(session, item_id, current_user)
        # Soft delete — archive instead of hard delete for audit trail
        item.status = "archived"
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
