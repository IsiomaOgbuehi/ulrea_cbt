from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func

from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel, SubjectAssignment
from cbt_service.schemas.item_subject_schemas import ItemCreate, ItemUpdate, CurrentUser
from cbt_service.database.models.enums.item_subject_enums import ItemSource, ItemStatus, ItemType
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.services.item.utils.item_answer_utils import normalize_correct_answers, assert_correct_answers_valid
from cbt_service.services.item.scope_service import ScopeService
from cbt_service.services.period_service import PeriodScope
from cbt_service.services.difficulty_service import DifficultyScope
from cbt_service.core.permissions import P
from cbt_service.database.models.bank_topic import ItemTopic
from cbt_service.database.models.attempt import ResponseModel
from cbt_service.database.models.exam import ExamAuditLog, ExamItem, ExamModel
from cbt_service.database.models.enums.exam_enum import ExamAction, ExamStatus
from cbt_service.schemas.item_subject_schemas import ItemRead, ItemUsageExam, ItemUsageRead

# Exams a question can still be taken off when it's deleted: nobody has
# reviewed, scheduled or sat them yet.
EDITABLE_EXAM_STATUSES: frozenset[ExamStatus] = frozenset({ExamStatus.DRAFT, ExamStatus.REJECTED})
from sqlmodel import col
from sqlalchemy import exists


class ItemService:

    @staticmethod
    def _assert_subject_access(
        session: Session, subject_id: UUID, current_user: CurrentUser, perm: P = P.QUESTIONS_VIEW
    ) -> SubjectModel:
        """
        Shared guard: the subject exists in the caller's org, and the caller holds
        `perm` for it (any subject with reach "all", else only assigned ones).
        """
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,
            )
        ).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        reach = current_user.reach(perm)
        if reach is None:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        if reach != "all":
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
        ItemService._assert_subject_access(session, subject_id, current_user, P.QUESTIONS_MANAGE)
        ScopeService.validate_bank(session, subject_id, payload.bank_id, current_user.org_id)
        topic_ids: list[UUID] = ScopeService.validate_topics(
            session, subject_id, payload.topic_ids, current_user.org_id
        )
        PeriodScope.validate(session, payload.period_id, current_user.org_id)
        difficulty: str = DifficultyScope.resolve(session, current_user.org_id, payload.difficulty)

        item = ItemModel(
            org_id=current_user.org_id,
            subject_id=subject_id,
            bank_id=payload.bank_id,
            period_id=payload.period_id,
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
            difficulty=difficulty,
        )
        session.add(item)
        session.flush()
        ScopeService.set_item_topics(session, item.id, topic_ids)
        session.commit()
        session.refresh(item)
        return item

    @staticmethod
    def get_all(
        session: Session,
        subject_id: UUID,
        current_user: CurrentUser,
        status: ItemStatus | None = None,
        difficulty: str | None = None,
        item_type: ItemType | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        bank_id: UUID | None = None,
        no_bank: bool = False,
        topic_id: UUID | None = None,
        period_id: UUID | None = None,
        no_period: bool = False,
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
        difficulty_key: str | None = DifficultyScope.filter_key(session, current_user.org_id, difficulty)
        if difficulty_key:
            query = query.where(ItemModel.difficulty == difficulty_key)
        if item_type:
            query = query.where(ItemModel.item_type == item_type)
        if search:
            query = query.where(ItemModel.question_text.ilike(f"%{search}%"))
        if no_bank:
            query = query.where(col(ItemModel.bank_id).is_(None))
        elif bank_id:
            query = query.where(ItemModel.bank_id == bank_id)
        if topic_id:
            query = query.where(
                exists().where(ItemTopic.item_id == ItemModel.id, ItemTopic.topic_id == topic_id)
            )
        if no_period:
            query = query.where(col(ItemModel.period_id).is_(None))
        elif period_id:
            # A session also matches the questions tagged with its terms.
            period_ids: list[UUID] = PeriodScope.with_children(session, period_id, current_user.org_id)
            query = query.where(col(ItemModel.period_id).in_(period_ids))

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
        perm: P = P.QUESTIONS_VIEW,
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
        ItemService._assert_subject_access(session, item.subject_id, current_user, perm)
        return item

    @staticmethod
    def update(
        session: Session,
        item_id: UUID,
        payload: ItemUpdate,
        current_user: CurrentUser,
    ) -> ItemModel:
        item = ItemService.get_by_id(session, item_id, current_user, P.QUESTIONS_MANAGE)

        update_data = payload.model_dump(exclude_unset=True)

        new_topic_ids: list[UUID] | None = update_data.pop("topic_ids", None)
        if new_topic_ids is not None:
            new_topic_ids = ScopeService.validate_topics(
                session, item.subject_id, new_topic_ids, current_user.org_id
            )
        if "bank_id" in update_data and update_data["bank_id"] != item.bank_id:
            ScopeService.validate_bank(session, item.subject_id, update_data["bank_id"], current_user.org_id)
        if "period_id" in update_data and update_data["period_id"] != item.period_id:
            PeriodScope.validate(session, update_data["period_id"], current_user.org_id)
        if "difficulty" in update_data:
            if update_data["difficulty"] is None:
                update_data.pop("difficulty")       # not clearable; leave it as it is
            else:
                update_data["difficulty"] = DifficultyScope.resolve(
                    session, current_user.org_id, update_data["difficulty"], current=item.difficulty,
                )

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
        if new_topic_ids is not None:
            ScopeService.set_item_topics(session, item.id, new_topic_ids)

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
        item = ItemService.get_by_id(session, item_id, current_user, P.QUESTIONS_MANAGE)

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
    def usage(session: Session, item_id: UUID, current_user: CurrentUser) -> ItemUsageRead:
        """Where a question is used, and whether it can be deleted for good."""
        item = ItemService.get_by_id(session, item_id, current_user, P.QUESTIONS_MANAGE)
        return ItemService._usage(session, item)

    @staticmethod
    def _usage(session: Session, item: ItemModel) -> ItemUsageRead:
        answered: int = int(session.exec(
            select(func.count()).select_from(ResponseModel).where(
                ResponseModel.item_id == item.id, ResponseModel.org_id == item.org_id,
            )
        ).one())
        exams: list[ExamModel] = list(session.exec(
            select(ExamModel)
            .where(
                col(ExamModel.id).in_(
                    select(ExamItem.exam_id).where(ExamItem.item_id == item.id, ExamItem.org_id == item.org_id)
                )
            )
            .order_by(col(ExamModel.created_at))
        ).all())
        listed = [
            ItemUsageExam(id=e.id, title=e.title, status=getattr(e.status, "value", str(e.status)))
            for e in exams
        ]

        reason: str | None = None
        if answered:
            reason = (
                f"Students have answered it {answered} time{'s' if answered != 1 else ''}. "
                "Deleting it would change their results, so archive it instead."
            )
        else:
            locked = [e for e in exams if ExamStatus(e.status) not in EDITABLE_EXAM_STATUSES]
            if locked:
                names = ", ".join(f"“{e.title}”" for e in locked[:3]) + (" and others" if len(locked) > 3 else "")
                reason = (
                    f"It's on {names}, which {'has' if len(locked) == 1 else 'have'} been submitted, approved "
                    "or run. Archive it instead, or take it off those exams first."
                )
        return ItemUsageRead(answered_count=answered, exams=listed, can_delete=reason is None, reason=reason)

    @staticmethod
    def delete(
        session: Session,
        item_id: UUID,
        current_user: CurrentUser,
    ) -> None:
        """
        Delete a question for good. Only when no student has answered it and it
        isn't on an exam past draft; it's taken off any draft exams. Otherwise
        409 — archive it instead (PATCH .../status), which keeps results intact.
        """
        item = ItemService.get_by_id(session, item_id, current_user, P.QUESTIONS_MANAGE)
        usage = ItemService._usage(session, item)
        if not usage.can_delete:
            raise HTTPException(status_code=409, detail=usage.reason)

        now = datetime.now(timezone.utc)
        exam_items: list[ExamItem] = list(session.exec(
            select(ExamItem).where(ExamItem.item_id == item.id, ExamItem.org_id == item.org_id)
        ).all())
        exams_by_id: dict[UUID, ExamModel] = {
            e.id: e for e in session.exec(
                select(ExamModel).where(col(ExamModel.id).in_({ei.exam_id for ei in exam_items}))
            ).all()
        } if exam_items else {}
        for exam_item in exam_items:
            exam = exams_by_id.get(exam_item.exam_id)
            if exam is not None:
                exam.total_marks = max(0.0, exam.total_marks - exam_item.marks)
                exam.updated_at = now
                session.add(exam)
            session.add(ExamAuditLog(
                exam_id=exam_item.exam_id, org_id=item.org_id, actor_id=current_user.id,
                action=ExamAction.ITEM_REMOVED.value,
                extra_data={"item_id": str(item.id), "reason": "question deleted"},
                created_at=now,
            ))
            session.delete(exam_item)
        for link in session.exec(select(ItemTopic).where(ItemTopic.item_id == item.id)).all():
            session.delete(link)
        session.flush()
        session.delete(item)
        session.commit()



    @staticmethod
    def to_reads(session: Session, items: list[ItemModel]) -> list[ItemRead]:
        """ItemRead for each item, with topic ids loaded in one query."""
        topics: dict[UUID, list[UUID]] = ScopeService.topic_ids_by_item(session, [i.id for i in items])
        return [
            ItemRead.model_validate({**i.model_dump(), "topic_ids": topics.get(i.id, [])})
            for i in items
        ]

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
