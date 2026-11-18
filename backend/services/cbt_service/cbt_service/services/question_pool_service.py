"""
Exam bodies' shared questions.

- Question library: an exam body shares whole question banks ("Share with
  other organisations"); organisations on a trial or paid plan browse the
  shared banks and copy questions, or a whole bank, into their own subjects,
  optionally straight into an exam. A copy is the organisation's own question
  and points at its original, so later edits or unsharing never change it.
- Topic tests: subscribed students practise a random set of questions from
  the body's shared banks and the exams it offers in External Exams, so a
  body with no approved exam yet can still be practised. Nothing is stored:
  the student's device keeps the test, and the server marks it.
"""
import random
from collections.abc import Iterable
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import Select
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import Session, col, func, or_, select

from cbt_service.core.permissions import P
from cbt_service.database.models.bank_topic import ItemTopic, QuestionBank, Topic
from cbt_service.database.models.enums.exam_enum import ExamStatus
from cbt_service.database.models.enums.item_subject_enums import BankStatus, ItemSource, ItemStatus, ItemType
from cbt_service.database.models.exam import ExamItem, ExamModel
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.exam_schemas import ExamItemAdd
from cbt_service.schemas.question_library_schemas import (
    MAX_BANK_COPY, LibraryBodyRead, LibraryCopyRequest, LibraryCopyResult, LibraryItemPage, LibraryItemRead,
    LibraryOptionRead, PoolBankRead, PoolSubjectRead, PoolTopicRead, TopicTestItemRead, TopicTestMarkedItem,
    TopicTestRead, TopicTestRequest, TopicTestResult, TopicTestScoreRequest,
)
from cbt_service.services.difficulty_service import DifficultyScope
from cbt_service.services.exam_service import ExamService
from cbt_service.services.item.item_service import ItemService
from cbt_service.services.item.scope_service import ScopeService
from cbt_service.services.marking.scoring import OBJECTIVE_TYPES, score_answer
from cbt_service.services.paging import total_pages

SHARED_EXAM_STATUSES: tuple[ExamStatus, ...] = (ExamStatus.APPROVED, ExamStatus.ACTIVE)


def _shared_item_ids(body_ids: Iterable[UUID]) -> Select[tuple[UUID]]:
    """Ids of the questions in the bodies' exams offered in External Exams (topic tests)."""
    return (
        select(ExamItem.item_id)
        .join(ExamModel, col(ExamModel.id) == col(ExamItem.exam_id))
        .where(
            col(ExamModel.org_id).in_(list(body_ids)),
            col(ExamModel.external_listed).is_(True),
            col(ExamModel.status).in_(SHARED_EXAM_STATUSES),
        )
    )


def _pool_filter(body_id: UUID, objective_only: bool = False) -> list[ColumnElement[bool]]:
    """Topic tests: questions in the body's shared banks or its External Exams exams."""
    conds: list[ColumnElement[bool]] = [
        ItemModel.org_id == body_id,
        ItemModel.status == ItemStatus.ACTIVE,
        or_(
            col(ItemModel.bank_id).in_(_shared_bank_ids([body_id])),
            col(ItemModel.id).in_(_shared_item_ids([body_id])),
        ),
    ]
    if objective_only:
        conds.append(col(ItemModel.item_type).in_(list(OBJECTIVE_TYPES)))
    return conds


def _shared_bank_ids(body_ids: Iterable[UUID]) -> Select[tuple[UUID]]:
    """The bodies' banks shared with other organisations (not archived)."""
    return select(QuestionBank.id).where(
        col(QuestionBank.org_id).in_(list(body_ids)),
        col(QuestionBank.shared).is_(True),
        QuestionBank.status == BankStatus.ACTIVE.value,
    )


def _library_filter(body_id: UUID) -> list[ColumnElement[bool]]:
    """Question library: active questions in the body's shared banks."""
    return [
        ItemModel.org_id == body_id,
        ItemModel.status == ItemStatus.ACTIVE,
        col(ItemModel.bank_id).in_(_shared_bank_ids([body_id])),
    ]


def _type(item: ItemModel) -> str:
    return ItemType(item.item_type).value


def _options(raw: list[dict[str, object]] | None) -> list[LibraryOptionRead] | None:
    return [
        LibraryOptionRead(key=str(o.get("key")), text=str(o.get("text", "")))
        for o in (raw or []) if isinstance(o, dict)
    ] or None


def _with_subtopics(session: Session, subject_id: UUID, body_id: UUID, topic_ids: list[UUID]) -> set[UUID]:
    """The chosen topics and every topic under them."""
    children: dict[UUID, list[UUID]] = {}
    for t in session.exec(select(Topic).where(Topic.subject_id == subject_id, Topic.org_id == body_id)).all():
        if t.parent_id is not None:
            children.setdefault(t.parent_id, []).append(t.id)
    out: set[UUID] = set()
    stack = list(topic_ids)
    while stack:
        tid = stack.pop()
        if tid not in out:
            out.add(tid)
            stack.extend(children.get(tid, []))
    return out


def _copies_of(session: Session, org_id: UUID, subject_id: UUID, original_ids: list[UUID]) -> dict[UUID, UUID]:
    """original id -> the organisation's copy of it in this subject."""
    if not original_ids:
        return {}
    out: dict[UUID, UUID] = {}
    for copy_id, original in session.exec(
        select(ItemModel.id, ItemModel.copied_from_item_id).where(
            ItemModel.org_id == org_id, ItemModel.subject_id == subject_id,
            col(ItemModel.copied_from_item_id).in_(original_ids),
        )
    ).all():
        if original is not None:
            out.setdefault(original, copy_id)
    return out


class QuestionPoolService:

    # ---- Browsing -----------------------------------------------------------

    @staticmethod
    def library_counts(session: Session, body_ids: list[UUID]) -> dict[UUID, int]:
        """Questions in shared banks per exam body (one query)."""
        if not body_ids:
            return {}
        rows = session.exec(
            select(ItemModel.org_id, func.count()).where(
                col(ItemModel.org_id).in_(body_ids), ItemModel.status == ItemStatus.ACTIVE,
                col(ItemModel.bank_id).in_(_shared_bank_ids(body_ids)),
            ).group_by(col(ItemModel.org_id))
        ).all()
        return {org_id: int(n) for org_id, n in rows}

    @staticmethod
    def library_bodies(session: Session, bodies: dict[UUID, str], own_org: UUID) -> list[LibraryBodyRead]:
        others = {b: name for b, name in bodies.items() if b != own_org}
        counts = QuestionPoolService.library_counts(session, list(others))
        return sorted(
            (LibraryBodyRead(id=b, name=name, question_count=counts.get(b, 0)) for b, name in others.items()),
            key=lambda r: r.name.casefold(),
        )

    @staticmethod
    def subjects(
        session: Session, body_id: UUID, objective_only: bool = False, library: bool = False,
    ) -> list[PoolSubjectRead]:
        """The body's subjects with questions to offer, each with its topics
        (and, for the question library, its shared banks) and counts."""
        conds = _library_filter(body_id) if library else _pool_filter(body_id, objective_only)
        per_subject: dict[UUID, int] = {
            sid: int(n) for sid, n in session.exec(
                select(ItemModel.subject_id, func.count()).where(*conds).group_by(col(ItemModel.subject_id))
            ).all()
        }
        if not per_subject:
            return []
        per_topic: dict[UUID, int] = {
            tid: int(n) for tid, n in session.exec(
                select(ItemTopic.topic_id, func.count())
                .join(ItemModel, col(ItemModel.id) == col(ItemTopic.item_id))
                .where(*conds).group_by(col(ItemTopic.topic_id))
            ).all()
        }
        subjects = session.exec(select(SubjectModel).where(col(SubjectModel.id).in_(list(per_subject)))).all()
        topics = session.exec(
            select(Topic).where(col(Topic.subject_id).in_(list(per_subject)), Topic.org_id == body_id)
            .order_by(col(Topic.position), col(Topic.name))
        ).all()
        by_subject: dict[UUID, list[PoolTopicRead]] = {}
        for t in topics:
            if t.id in per_topic:
                by_subject.setdefault(t.subject_id, []).append(
                    PoolTopicRead(id=t.id, name=t.name, parent_id=t.parent_id, question_count=per_topic[t.id])
                )
        banks_by_subject: dict[UUID, list[PoolBankRead]] = {}
        if library:
            per_bank: dict[UUID, int] = {
                bid: int(n) for bid, n in session.exec(
                    select(ItemModel.bank_id, func.count()).where(*conds).group_by(col(ItemModel.bank_id))
                ).all() if bid is not None
            }
            for b in session.exec(
                select(QuestionBank).where(col(QuestionBank.id).in_(list(per_bank)))
                .order_by(col(QuestionBank.position), col(QuestionBank.name))
            ).all():
                banks_by_subject.setdefault(b.subject_id, []).append(
                    PoolBankRead(id=b.id, name=b.name, kind=b.kind, year=b.year, question_count=per_bank[b.id])
                )
        return sorted(
            (
                PoolSubjectRead(
                    id=s.id, name=s.name, question_count=per_subject[s.id], topics=by_subject.get(s.id, []),
                    banks=banks_by_subject.get(s.id, []),
                )
                for s in subjects
            ),
            key=lambda r: r.name.casefold(),
        )

    @staticmethod
    def library_items(
        session: Session, body_id: UUID, *, subject_id: UUID | None, topic_id: UUID | None, bank_id: UUID | None,
        search: str | None, page: int, page_size: int, into_subject_id: UUID | None, org_id: UUID,
    ) -> LibraryItemPage:
        conds = _library_filter(body_id)
        if subject_id is not None:
            conds.append(ItemModel.subject_id == subject_id)
        if bank_id is not None:
            conds.append(ItemModel.bank_id == bank_id)
        if topic_id is not None and subject_id is not None:
            topic_set = _with_subtopics(session, subject_id, body_id, [topic_id])
            conds.append(col(ItemModel.id).in_(select(ItemTopic.item_id).where(col(ItemTopic.topic_id).in_(topic_set))))
        if search and search.strip():
            conds.append(col(ItemModel.question_text).ilike(f"%{search.strip()}%"))

        total = int(session.exec(select(func.count()).select_from(ItemModel).where(*conds)).one())
        rows = list(session.exec(
            select(ItemModel).where(*conds).order_by(col(ItemModel.created_at).desc(), col(ItemModel.id))
            .offset((page - 1) * page_size).limit(page_size)
        ).all())
        ids = [i.id for i in rows]
        subject_names: dict[UUID, str] = {
            s.id: s.name for s in session.exec(
                select(SubjectModel).where(col(SubjectModel.id).in_({i.subject_id for i in rows}))
            ).all()
        } if rows else {}
        bank_names: dict[UUID, str] = {
            b.id: b.name for b in session.exec(
                select(QuestionBank).where(col(QuestionBank.id).in_({i.bank_id for i in rows if i.bank_id}))
            ).all()
        } if rows else {}
        topic_names: dict[UUID, list[str]] = {}
        if ids:
            for item_id, name in session.exec(
                select(ItemTopic.item_id, Topic.name).join(Topic, col(Topic.id) == col(ItemTopic.topic_id))
                .where(col(ItemTopic.item_id).in_(ids))
            ).all():
                topic_names.setdefault(item_id, []).append(name)
        copied: set[UUID] = set(_copies_of(session, org_id, into_subject_id, ids)) if into_subject_id else set()
        return LibraryItemPage(
            items=[
                LibraryItemRead(
                    id=i.id, subject_name=subject_names.get(i.subject_id, ""),
                    bank_name=bank_names.get(i.bank_id) if i.bank_id else None,
                    question_text=i.question_text, item_type=_type(i),
                    options=_options(i.options), correct_answers=i.correct_answers, explanation=i.explanation,
                    marks=i.marks, difficulty=i.difficulty, topics=sorted(topic_names.get(i.id, [])),
                    already_copied=i.id in copied,
                )
                for i in rows
            ],
            total=total, page=page, page_size=page_size, pages=total_pages(total, page_size),
        )

    # ---- Copying (question library) ------------------------------------------

    @staticmethod
    def _bank_for_copy(
        session: Session, source: QuestionBank, subject_id: UUID, current_user: CurrentUser,
    ) -> UUID:
        """A bank in the subject with the source bank's name: the existing one, or a new one."""
        existing = session.exec(
            select(QuestionBank).where(
                QuestionBank.org_id == current_user.org_id, QuestionBank.subject_id == subject_id,
                QuestionBank.name == source.name,
            )
        ).first()
        if existing is not None:
            if existing.status != BankStatus.ACTIVE.value:
                raise HTTPException(status_code=409, detail=f"Your bank '{existing.name}' is archived. Restore it first.")
            ScopeService.assert_bank_access(session, existing.id, current_user, P.QUESTIONS_MANAGE)
            return existing.id
        ItemService._assert_subject_access(session, subject_id, current_user, P.BANKS_TOPICS_MANAGE)
        bank = QuestionBank(
            org_id=current_user.org_id, subject_id=subject_id, name=source.name, kind=source.kind,
            year=source.year, description=source.description, created_by=current_user.id,
        )
        session.add(bank)
        session.flush()
        return bank.id

    @staticmethod
    def copy(session: Session, payload: LibraryCopyRequest, current_user: CurrentUser) -> LibraryCopyResult:
        """Copy shared questions (or a whole shared bank) into a subject, and
        optionally add them to an exam (copying first, so the exam only ever
        holds the organisation's own questions)."""
        body_id = payload.exam_body_org_id
        if body_id == current_user.org_id:
            raise HTTPException(status_code=400, detail="These are already your organisation's questions.")

        exam: ExamModel | None = None
        if payload.exam_id is not None:
            exam = ExamService._get_exam(session, payload.exam_id, current_user.org_id)
            ExamService._assert_can_edit(session, exam, current_user)
            if exam.subject_id is None:
                raise HTTPException(status_code=400, detail="Give the exam a subject first.")
            if payload.subject_id is not None and payload.subject_id != exam.subject_id:
                raise HTTPException(status_code=400, detail="Questions for an exam go into the exam's subject.")
        subject_id: UUID = exam.subject_id if exam is not None and exam.subject_id else payload.subject_id  # type: ignore[assignment]
        ItemService._assert_subject_access(session, subject_id, current_user, P.QUESTIONS_MANAGE)
        topic_ids = ScopeService.validate_topics(
            session, subject_id, [payload.topic_id] if payload.topic_id else [], current_user.org_id,
        )

        conds = _library_filter(body_id)
        if payload.source_bank_id is not None:
            source = session.exec(
                select(QuestionBank).where(
                    QuestionBank.id == payload.source_bank_id, col(QuestionBank.id).in_(_shared_bank_ids([body_id])),
                )
            ).first()
            if source is None:
                raise HTTPException(status_code=404, detail="That bank isn't shared any more.")
            conds.append(ItemModel.bank_id == source.id)
            originals = list(session.exec(
                select(ItemModel).where(*conds).order_by(col(ItemModel.created_at), col(ItemModel.id))
                .limit(MAX_BANK_COPY + 1)
            ).all())
            if len(originals) > MAX_BANK_COPY:
                raise HTTPException(status_code=400, detail=f"Copy at most {MAX_BANK_COPY} questions at a time.")
            target_bank: UUID | None = (
                QuestionPoolService._bank_for_copy(session, source, subject_id, current_user) if payload.new_bank
                else payload.bank_id
            )
        else:
            wanted = list(dict.fromkeys(payload.item_ids or []))
            found = {
                i.id: i for i in session.exec(select(ItemModel).where(*conds, col(ItemModel.id).in_(wanted))).all()
            }
            originals = [found[i] for i in wanted if i in found]          # keep the order chosen
            target_bank = payload.bank_id
        if not payload.new_bank:
            ScopeService.validate_bank(session, subject_id, target_bank, current_user.org_id)
            ScopeService.assert_bank_access(session, target_bank, current_user, P.QUESTIONS_MANAGE)

        already = _copies_of(session, current_user.org_id, subject_id, [o.id for o in originals])
        resolve = DifficultyScope.bulk_resolver(session, current_user.org_id)
        default_key, _ = resolve(None)
        copies: list[ItemModel] = []
        for o in originals:
            if o.id in already:
                continue
            key, err = resolve(o.difficulty)
            difficulty = key if err is None and key else default_key
            if difficulty is None:
                raise HTTPException(status_code=422, detail="Set up at least one difficulty level first.")
            copies.append(ItemModel(
                org_id=current_user.org_id, subject_id=subject_id, bank_id=target_bank,
                created_by=current_user.id, source=ItemSource.IMPORTED, copied_from_item_id=o.id,
                question_text=o.question_text, item_type=o.item_type, options=o.options,
                correct_answers=o.correct_answers, explanation=o.explanation, marking_guide=o.marking_guide,
                word_limit=o.word_limit, marks=o.marks, negative_marks=o.negative_marks,
                tags=list(o.tags or []), difficulty=difficulty, status=ItemStatus.ACTIVE,
            ))
        session.add_all(copies)
        session.flush()
        for c in copies:
            for tid in topic_ids:
                session.add(ItemTopic(item_id=c.id, topic_id=tid))
        session.commit()
        requested = len(payload.item_ids or []) if payload.item_ids is not None else len(originals)
        result = LibraryCopyResult(copied=len(copies), skipped=requested - len(copies), bank_id=target_bank)

        if exam is not None and originals:
            # This copy, or one made earlier, of every question chosen, in the order chosen.
            new_copies = {c.copied_from_item_id: c.id for c in copies}
            ids = [new_copies.get(o.id) or already[o.id] for o in originals]
            before = len(session.exec(select(ExamItem.id).where(ExamItem.exam_id == exam.id)).all())
            ExamService.add_items(session, exam.id, ExamItemAdd(item_ids=ids), current_user)
            after = len(session.exec(select(ExamItem.id).where(ExamItem.exam_id == exam.id)).all())
            result.added_to_exam = after - before
        return result

    # ---- Topic tests (students) ----------------------------------------------

    @staticmethod
    def draw(session: Session, body_id: UUID, payload: TopicTestRequest) -> TopicTestRead:
        """A random set of objective questions from the chosen topics (no answers)."""
        subject = session.exec(
            select(SubjectModel).where(SubjectModel.id == payload.subject_id, SubjectModel.org_id == body_id)
        ).first()
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found.")
        conds = _pool_filter(body_id, objective_only=True)
        conds.append(ItemModel.subject_id == subject.id)
        if payload.topic_ids:
            topic_set = _with_subtopics(session, subject.id, body_id, payload.topic_ids)
            conds.append(col(ItemModel.id).in_(select(ItemTopic.item_id).where(col(ItemTopic.topic_id).in_(topic_set))))
        # Ids only, then a random sample: cheap even for large pools.
        ids = list(session.exec(select(ItemModel.id).where(*conds)).all())
        if not ids:
            raise HTTPException(status_code=404, detail="No questions for these topics yet.")
        chosen = random.sample(ids, min(payload.count, len(ids)))
        items = {i.id: i for i in session.exec(select(ItemModel).where(col(ItemModel.id).in_(chosen))).all()}
        return TopicTestRead(
            subject_name=subject.name,
            items=[
                TopicTestItemRead(
                    id=i.id, question_text=i.question_text,
                    item_type=_type(i),
                    options=_options(i.options), marks=i.marks,
                )
                for i in (items[c] for c in chosen if c in items)
            ],
        )

    @staticmethod
    def score(session: Session, body_id: UUID, payload: TopicTestScoreRequest) -> TopicTestResult:
        """Mark a topic test the student's device sends. Nothing is stored."""
        answers: dict[UUID, list[str] | None] = {a.item_id: a.answer for a in payload.answers}
        items = {
            i.id: i for i in session.exec(
                select(ItemModel).where(*_pool_filter(body_id, objective_only=True), col(ItemModel.id).in_(list(answers)))
            ).all()
        }
        marked: list[TopicTestMarkedItem] = []
        score = max_score = 0.0
        for item_id, answer in answers.items():
            item = items.get(item_id)
            if item is None:
                continue
            item_type = _type(item)
            s = score_answer(item_type, item.correct_answers, answer, item.marks, item.negative_marks)
            awarded = s.marks_awarded or 0.0
            score += awarded
            max_score += item.marks
            marked.append(TopicTestMarkedItem(
                item_id=item_id, your_answer=answer, correct_answers=item.correct_answers,
                is_correct=bool(s.is_correct), marks_awarded=awarded, marks=item.marks,
                explanation=item.explanation,
            ))
        score = max(score, 0.0)
        return TopicTestResult(
            score=score, max_score=max_score,
            percentage=round(score / max_score * 100, 1) if max_score > 0 else 0.0,
            items=marked, missing=len(answers) - len(marked),
        )

