from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func

from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel, SubjectAssignment
from cbt_service.schemas.item_subject_schemas import ItemCreate, ItemUpdate, CurrentUser
from cbt_service.database.models.enums.item_subject_enums import ItemDifficulty, ItemSource, ItemStatus, ItemType
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.services.item.utils.item_answer_utils import normalize_correct_answers, assert_correct_answers_valid


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
            source=ItemSource.MANUAL,
            question_text=payload.question_text,
            item_type=payload.item_type,
            # Convert Pydantic objects to plain dicts for JSON storage
            options=[opt.model_dump() for opt in payload.options] if payload.options else None,
            correct_answers=payload.correct_answers,
            explanation=payload.explanation,
            marks=payload.marks,
            negative_marks=payload.negative_marks,
            tags=payload.tags or [],
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
        status: ItemStatus | None = None,
        difficulty: ItemDifficulty | None = None,
        item_type: ItemType | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ItemModel], int]:
        ItemService._assert_subject_access(session, subject_id, current_user)

        query = select(ItemModel).where(
            ItemModel.subject_id == subject_id,
            ItemModel.org_id == current_user.org_id,
        )

        # Teachers only see their own items; admins and above see everything
        # if current_user.role == UserRole.TEACHER:
        #     query = query.where(ItemModel.created_by == current_user.id)

        if status:
            query = query.where(ItemModel.status == status)
        else:
            query = query.where(ItemModel.status == ItemStatus.ACTIVE)
        if difficulty:
            query = query.where(ItemModel.difficulty == difficulty)
        if item_type:
            query = query.where(ItemModel.item_type == item_type)
        if search:
            query = query.where(ItemModel.question_text.ilike(f"%{search}%"))

        # Count total matching rows BEFORE applying limit/offset
        total = session.exec(
            select(func.count()).select_from(query.subquery())
        ).one()

        paginated_query = (
            query
            .order_by(ItemModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        items = session.exec(paginated_query).all()
        return items, total

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

        # Re-validate correct_answers against options whenever either changes —
        # use the incoming value if provided, otherwise fall back to what's
        # already stored, so partial updates can't desync the two.

        # if "options" in update_data or "correct_answers" in update_data:
        #     new_options = update_data.get("options", item.options)
        #     new_correct = normalize_correct_answers(new_options, new_correct)
        #     assert_correct_answers_valid(new_options, new_correct)
        #     update_data["correct_answers"] = new_correct

        if "options" in update_data or "correct_answers" in update_data:
            new_options = update_data.get("options", item.options)
            incoming_correct = update_data.get("correct_answers", item.correct_answers)
            new_correct = normalize_correct_answers(new_options, incoming_correct)
            assert_correct_answers_valid(new_options, new_correct)
            update_data["correct_answers"] = new_correct

        for key, value in update_data.items():
            setattr(item, key, value)

        item.version += 1
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


    @staticmethod
    def update_status(
        session: Session,
        item_id: UUID,
        new_status: ItemStatus,
        current_user: CurrentUser,
    ) -> ItemModel:
        item = ItemService.get_by_id(session, item_id, current_user)

        if item.status == new_status:
            return item  # no-op, avoid a pointless write + updated_at churn

        ItemService._assert_valid_transition(item.status, new_status, current_user)

        item.status = new_status
        item.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def _assert_valid_transition(
        current_status: ItemStatus,
        new_status: ItemStatus,
        current_user: CurrentUser,
    ) -> None:
        # Adjust to match your actual ItemStatus members and workflow
        ALLOWED_TRANSITIONS: dict[ItemStatus, set[ItemStatus]] = {
            ItemStatus.DRAFT: {ItemStatus.ACTIVE, ItemStatus.ARCHIVED},
            ItemStatus.ACTIVE: {ItemStatus.ARCHIVED, ItemStatus.DRAFT},
            ItemStatus.ARCHIVED: {ItemStatus.DRAFT},  # restoring from archive
        }

        if new_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change status from {current_status} to {new_status}",
            )

        # Example role gate: only admins can restore archived items
        # if current_status == ItemStatus.ARCHIVED and current_user.role == UserRole.TEACHER:
        #     raise HTTPException(
        #         status_code=403,
        #         detail="Only admins can restore archived items",
        #     )



    @staticmethod
    def delete(
        session: Session,
        item_id: UUID,
        current_user: CurrentUser,
    ) -> None:
        # Soft delete — archive instead of hard delete for audit trail
        ItemService.update_status(session, item_id, ItemStatus.ARCHIVED, current_user)



    @staticmethod
    def get_by_ids_for_scoring(session: Session, item_ids: list[UUID]) -> list[ItemModel]:
        """Internal only — includes correct_answers. Caller must be a trusted service."""
        if not item_ids:
            return []
        return session.exec(
            select(ItemModel).where(ItemModel.id.in_(item_ids))
        ).all()

    @staticmethod
    def get_by_ids_for_display(session: Session, item_ids: list[UUID]) -> list[ItemModel]:
        """
        Student-facing content. Answer-key exclusion happens at the schema layer
        (ItemForDisplayRead has no correct_answers field), but this method exists
        as a separate entry point so scoring and display never accidentally share
        a response model.
        """
        if not item_ids:
            return []
        return session.exec(
            select(ItemModel).where(ItemModel.id.in_(item_ids))
        ).all()
