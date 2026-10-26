"""
Helpers shared by items, exams and bulk upload for banks and topics.
Kept free of ItemService imports so item_service can use it without a cycle.
"""
from collections import defaultdict
from collections.abc import Iterable, Sequence
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import delete as sa_delete
from sqlmodel import Session, col, select

from cbt_service.database.models.bank_topic import ExamTopic, ItemTopic, QuestionBank, Topic
from cbt_service.database.models.enums.item_subject_enums import BankStatus



class ScopeService:

    @staticmethod
    def get_bank(session: Session, subject_id: UUID, bank_id: UUID, org_id: UUID) -> QuestionBank:
        bank: QuestionBank | None = session.exec(
            select(QuestionBank).where(
                QuestionBank.id == bank_id,
                QuestionBank.subject_id == subject_id,
                QuestionBank.org_id == org_id,
            )
        ).first()
        if bank is None:
            raise HTTPException(status_code=400, detail="Bank not found in this subject.")
        return bank

    @staticmethod
    def validate_bank(session: Session, subject_id: UUID, bank_id: UUID | None, org_id: UUID) -> None:
        """A bank used for new content must exist in the subject and not be archived."""
        if bank_id is None:
            return
        bank = ScopeService.get_bank(session, subject_id, bank_id, org_id)
        if bank.status == BankStatus.ARCHIVED.value:
            raise HTTPException(status_code=400, detail=f"Bank '{bank.name}' is archived.")

    @staticmethod
    def validate_topics(
        session: Session, subject_id: UUID, topic_ids: Iterable[UUID], org_id: UUID
    ) -> list[UUID]:
        """De-duplicated topic ids, all guaranteed to belong to the subject."""
        wanted: list[UUID] = list(dict.fromkeys(topic_ids))
        if not wanted:
            return []
        found: set[UUID] = set(
            session.exec(
                select(Topic.id).where(
                    col(Topic.id).in_(wanted),
                    Topic.subject_id == subject_id,
                    Topic.org_id == org_id,
                )
            ).all()
        )
        missing: list[str] = [str(t) for t in wanted if t not in found]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={"message": "Some topics don't belong to this subject.", "topic_ids": missing},
            )
        return wanted

    @staticmethod
    def set_item_topics(session: Session, item_id: UUID, topic_ids: Sequence[UUID]) -> None:
        """Replace an item's topics (caller commits)."""
        session.exec(sa_delete(ItemTopic).where(col(ItemTopic.item_id) == item_id))  # type: ignore[call-overload]
        for topic_id in topic_ids:
            session.add(ItemTopic(item_id=item_id, topic_id=topic_id))

    @staticmethod
    def set_exam_topics(session: Session, exam_id: UUID, topic_ids: Sequence[UUID]) -> None:
        """Replace an exam's topic scope (caller commits)."""
        session.exec(sa_delete(ExamTopic).where(col(ExamTopic.exam_id) == exam_id))  # type: ignore[call-overload]
        for topic_id in topic_ids:
            session.add(ExamTopic(exam_id=exam_id, topic_id=topic_id))

    @staticmethod
    def topic_ids_by_item(session: Session, item_ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = defaultdict(list)
        if not item_ids:
            return result
        rows = session.exec(
            select(ItemTopic.item_id, ItemTopic.topic_id).where(col(ItemTopic.item_id).in_(item_ids))
        ).all()
        for item_id, topic_id in rows:
            result[item_id].append(topic_id)
        return result

    @staticmethod
    def topic_ids_by_exam(session: Session, exam_ids: Sequence[UUID]) -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = defaultdict(list)
        if not exam_ids:
            return result
        rows = session.exec(
            select(ExamTopic.exam_id, ExamTopic.topic_id).where(col(ExamTopic.exam_id).in_(exam_ids))
        ).all()
        for exam_id, topic_id in rows:
            result[exam_id].append(topic_id)
        return result

    @staticmethod
    def topic_lookup(session: Session, subject_id: UUID, org_id: UUID) -> dict[str, UUID]:
        """
        Case-insensitive names -> topic id, for matching spreadsheet text.
        Each topic is reachable by its own name and by "Parent > Child".
        A bare name shared by two topics is ambiguous and left out (the
        "Parent > Child" form still works).
        """
        topics: list[Topic] = list(
            session.exec(select(Topic).where(Topic.subject_id == subject_id, Topic.org_id == org_id)).all()
        )
        by_id: dict[UUID, Topic] = {t.id: t for t in topics}
        lookup: dict[str, UUID] = {}
        ambiguous: set[str] = set()
        for topic in topics:
            key = topic.name.casefold()
            if key in lookup and lookup[key] != topic.id:
                ambiguous.add(key)
            lookup[key] = topic.id
            parent = by_id.get(topic.parent_id) if topic.parent_id else None
            if parent is not None:
                lookup[f"{parent.name.casefold()} > {key}"] = topic.id
        for key in ambiguous:
            lookup.pop(key, None)
        return lookup


