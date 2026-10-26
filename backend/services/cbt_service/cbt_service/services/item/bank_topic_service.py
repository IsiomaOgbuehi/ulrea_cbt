"""Banks and topics inside a subject."""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from cbt_service.database.models.bank_topic import ExamTopic, ItemTopic, QuestionBank, Topic
from cbt_service.database.models.enums.item_subject_enums import BankStatus, ItemStatus
from cbt_service.database.models.item import ItemModel
from cbt_service.schemas.bank_topic_schemas import (
    BankCreate, BankRead, BankUpdate, TopicCreate, TopicRead, TopicUpdate,
)
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.services.item.item_service import ItemService
from cbt_service.services.item.scope_service import ScopeService


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ====================================================================
# Banks
# ====================================================================

class BankService:

    @staticmethod
    def _item_counts(session: Session, subject_id: UUID) -> dict[UUID, int]:
        rows = session.exec(
            select(ItemModel.bank_id, func.count())
            .where(ItemModel.subject_id == subject_id, ItemModel.status == ItemStatus.ACTIVE)
            .where(col(ItemModel.bank_id).is_not(None))
            .group_by(col(ItemModel.bank_id))
        ).all()
        return {bank_id: int(count) for bank_id, count in rows if bank_id is not None}

    @staticmethod
    def _to_read(bank: QuestionBank, item_count: int = 0) -> BankRead:
        return BankRead.model_validate({**bank.model_dump(), "item_count": item_count})

    @staticmethod
    def list(
        session: Session, subject_id: UUID, current_user: CurrentUser, include_archived: bool = False
    ) -> list[BankRead]:
        ItemService._assert_subject_access(session, subject_id, current_user)
        query = select(QuestionBank).where(
            QuestionBank.subject_id == subject_id, QuestionBank.org_id == current_user.org_id
        )
        if not include_archived:
            query = query.where(QuestionBank.status == BankStatus.ACTIVE.value)
        banks = session.exec(query.order_by(col(QuestionBank.position), col(QuestionBank.name))).all()
        counts = BankService._item_counts(session, subject_id)
        return [BankService._to_read(b, counts.get(b.id, 0)) for b in banks]

    @staticmethod
    def create(session: Session, subject_id: UUID, payload: BankCreate, current_user: CurrentUser) -> BankRead:
        ItemService._assert_subject_access(session, subject_id, current_user)
        bank = QuestionBank(
            org_id=current_user.org_id,
            subject_id=subject_id,
            created_by=current_user.id,
            name=payload.name,
            kind=payload.kind.value,
            year=payload.year,
            description=payload.description,
            position=payload.position,
        )
        session.add(bank)
        BankService._commit_unique(session, payload.name)
        session.refresh(bank)
        return BankService._to_read(bank)

    @staticmethod
    def update(
        session: Session, subject_id: UUID, bank_id: UUID, payload: BankUpdate, current_user: CurrentUser
    ) -> BankRead:
        ItemService._assert_subject_access(session, subject_id, current_user)
        bank = ScopeService.get_bank(session, subject_id, bank_id, current_user.org_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            if key in ("kind", "status") and value is not None:
                value = value.value
            setattr(bank, key, value)
        bank.updated_at = _now()
        session.add(bank)
        BankService._commit_unique(session, bank.name)
        session.refresh(bank)
        return BankService._to_read(bank, BankService._item_counts(session, subject_id).get(bank.id, 0))

    @staticmethod
    def archive(session: Session, subject_id: UUID, bank_id: UUID, current_user: CurrentUser) -> BankRead:
        """Soft delete: questions keep their bank, it just stops being offered."""
        return BankService.update(
            session, subject_id, bank_id, BankUpdate(status=BankStatus.ARCHIVED), current_user
        )

    @staticmethod
    def _commit_unique(session: Session, name: str) -> None:
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(status_code=409, detail=f"A bank named '{name}' already exists in this subject.")


# ====================================================================
# Topics
# ====================================================================

class TopicService:

    @staticmethod
    def _get(session: Session, subject_id: UUID, topic_id: UUID, org_id: UUID) -> Topic:
        topic: Topic | None = session.exec(
            select(Topic).where(Topic.id == topic_id, Topic.subject_id == subject_id, Topic.org_id == org_id)
        ).first()
        if topic is None:
            raise HTTPException(status_code=404, detail="Topic not found.")
        return topic

    @staticmethod
    def _item_counts(session: Session, subject_id: UUID) -> dict[UUID, int]:
        rows = session.exec(
            select(ItemTopic.topic_id, func.count())
            .join(ItemModel, col(ItemModel.id) == col(ItemTopic.item_id))
            .where(ItemModel.subject_id == subject_id, ItemModel.status == ItemStatus.ACTIVE)
            .group_by(col(ItemTopic.topic_id))
        ).all()
        return {topic_id: int(count) for topic_id, count in rows}

    @staticmethod
    def _to_read(topic: Topic, item_count: int = 0) -> TopicRead:
        return TopicRead.model_validate({**topic.model_dump(), "item_count": item_count})

    @staticmethod
    def _assert_unique_sibling(
        session: Session, subject_id: UUID, parent_id: UUID | None, name: str, exclude_id: UUID | None = None
    ) -> None:
        query = select(Topic.id).where(
            Topic.subject_id == subject_id,
            func.lower(Topic.name) == name.casefold(),
            (col(Topic.parent_id).is_(None) if parent_id is None else Topic.parent_id == parent_id),
        )
        if exclude_id is not None:
            query = query.where(Topic.id != exclude_id)
        if session.exec(query).first() is not None:
            raise HTTPException(status_code=409, detail=f"Topic '{name}' already exists here.")

    @staticmethod
    def list(session: Session, subject_id: UUID, current_user: CurrentUser) -> list[TopicRead]:
        """Flat list ordered for display; build the tree from parent_id."""
        ItemService._assert_subject_access(session, subject_id, current_user)
        topics = session.exec(
            select(Topic)
            .where(Topic.subject_id == subject_id, Topic.org_id == current_user.org_id)
            .order_by(col(Topic.position), col(Topic.name))
        ).all()
        counts = TopicService._item_counts(session, subject_id)
        return [TopicService._to_read(t, counts.get(t.id, 0)) for t in topics]

    @staticmethod
    def create(session: Session, subject_id: UUID, payload: TopicCreate, current_user: CurrentUser) -> TopicRead:
        ItemService._assert_subject_access(session, subject_id, current_user)
        if payload.parent_id is not None:
            TopicService._get(session, subject_id, payload.parent_id, current_user.org_id)
        TopicService._assert_unique_sibling(session, subject_id, payload.parent_id, payload.name)
        topic = Topic(
            org_id=current_user.org_id,
            subject_id=subject_id,
            parent_id=payload.parent_id,
            name=payload.name,
            position=payload.position,
            created_by=current_user.id,
        )
        session.add(topic)
        session.commit()
        session.refresh(topic)
        return TopicService._to_read(topic)

    @staticmethod
    def update(
        session: Session, subject_id: UUID, topic_id: UUID, payload: TopicUpdate, current_user: CurrentUser
    ) -> TopicRead:
        ItemService._assert_subject_access(session, subject_id, current_user)
        topic = TopicService._get(session, subject_id, topic_id, current_user.org_id)
        data = payload.model_dump(exclude_unset=True)

        if "parent_id" in data:
            new_parent: UUID | None = data["parent_id"]
            # Walk up from the new parent: moving a topic under itself or one of
            # its own children would create a loop.
            cursor = new_parent
            while cursor is not None:
                if cursor == topic.id:
                    raise HTTPException(status_code=400, detail="A topic can't be moved under itself.")
                cursor = TopicService._get(session, subject_id, cursor, current_user.org_id).parent_id

        name: str = data.get("name") or topic.name
        parent_id: UUID | None = data["parent_id"] if "parent_id" in data else topic.parent_id
        TopicService._assert_unique_sibling(session, subject_id, parent_id, name, exclude_id=topic.id)

        for key, value in data.items():
            setattr(topic, key, value)
        topic.updated_at = _now()
        session.add(topic)
        session.commit()
        session.refresh(topic)
        return TopicService._to_read(topic, TopicService._item_counts(session, subject_id).get(topic.id, 0))

    @staticmethod
    def delete(session: Session, subject_id: UUID, topic_id: UUID, current_user: CurrentUser) -> None:
        """Deletes the topic and untags its questions/exams. Sub-topics must go first."""
        ItemService._assert_subject_access(session, subject_id, current_user)
        topic = TopicService._get(session, subject_id, topic_id, current_user.org_id)
        has_children = session.exec(select(Topic.id).where(Topic.parent_id == topic.id)).first()
        if has_children is not None:
            raise HTTPException(status_code=409, detail="Delete or move this topic's sub-topics first.")
        session.exec(sa_delete(ItemTopic).where(col(ItemTopic.topic_id) == topic.id))  # type: ignore[call-overload]
        session.exec(sa_delete(ExamTopic).where(col(ExamTopic.topic_id) == topic.id))  # type: ignore[call-overload]
        session.delete(topic)
        session.commit()
