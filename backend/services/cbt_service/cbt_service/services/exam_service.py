import logging
from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException, logger
from sqlmodel import Session, select, delete as sql_delete, col, func
from sqlalchemy import or_, and_, desc, false
from typing import Any
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cbt_service.services.item.utils.search import escape_like
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.enums.exam_enum import ExamAction
from cbt_service.database.models.subject import SubjectAssignment, SubjectModel
from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.exam import (
    ExamModel, ExamSection, ExamItem, ExamAssignment, ExamAuditLog
)
from cbt_service.database.models.enums.exam_enum import ExamStatus, AssignmentStatus
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.schemas.exam_schemas import ExamMaxAttemptsUpdate, ExamShowResultsUpdate
from cbt_service.database.models.enums.attempt_enum import AttemptStatus
from cbt_service.schemas.exam_schemas import (
    ExamCreate, ExamDeleteResponse, ExamItemInternalRead, ExamSectionInternalRead, ExamUpdate, ExamSectionCreate,
    ExamItemAdd, AssignStudentsRequest, ApprovalAction, CurrentUser, MyAssignmentRead, ExamRead,
    ExamPhase, ExamPhaseCounts,
)
from cbt_service.schemas.bank_topic_schemas import ExamItemsFromBank, ExamItemsFromBankResult
from cbt_service.services.item.scope_service import ScopeService
from cbt_service.services.period_service import PeriodScope
from cbt_service.services.difficulty_service import DifficultyScope
from cbt_service.core.permissions import P
from cbt_service.database.models.bank_topic import ItemTopic
from cbt_service.database.models.enums.item_subject_enums import ItemStatus, ItemType
from sqlalchemy import exists
from sqlmodel.sql.expression import Select, SelectOfScalar


def _log(
    session: Session,
    exam_id: UUID,
    org_id: UUID,
    actor_id: UUID,
    action: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    entry = ExamAuditLog(
        exam_id=exam_id,
        org_id=org_id,
        actor_id=actor_id,
        # Always the value ("created"), never the enum name.
        action=action.value if isinstance(action, ExamAction) else str(action),
        extra_data=metadata,     # was passed as `metadata`, which the model silently ignored
    )
    session.add(entry)

logger: logging.Logger = logging.getLogger(__name__)
_ASSIGN_CHUNK_SIZE: int = 1000  # keeps each statement far below the driver's bind-parameter cap


class ExamService:

    # --------------------------------------------------------
    # ACCESS GUARDS
    # --------------------------------------------------------

    @staticmethod
    def _get_exam(session: Session, exam_id: UUID, org_id: UUID) -> ExamModel:
        exam = session.exec(
            select(ExamModel).where(
                ExamModel.id == exam_id,
                ExamModel.org_id == org_id,         # tenant guard
            )
        ).first()
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")
        return exam


    @staticmethod
    def _assert_can_edit(session: Session, exam: ExamModel, current_user: CurrentUser) -> None:
        if not current_user.can(P.EXAMS_EDIT):
            raise HTTPException(status_code=403, detail="You don't have permission to edit exams.")
        if exam.status not in (ExamStatus.DRAFT, ExamStatus.REJECTED):
            raise HTTPException(status_code=400, detail=f"Exam cannot be edited in '{exam.status}' status.")
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_EDIT)


    # @staticmethod
    # def _assert_can_view(session: Session, exam: ExamModel, current_user: CurrentUser):
    #     """
    #     Creator, any teacher assigned to the exam's subject, or admin/super_admin
    #     can view the exam and its sections/items/assignments.
    #     Students are handled separately via ExamAssignment (see get_by_id).
    #     """
    #     if current_user.role != UserRole.TEACHER:
    #         return  # admin/super_admin unrestricted

    #     if exam.created_by == current_user.id:
    #         return

    #     assignment = session.exec(
    #         select(SubjectAssignment).where(
    #             SubjectAssignment.subject_id == exam.subject_id,
    #             SubjectAssignment.assigned_to == current_user.id,
    #             SubjectAssignment.org_id == current_user.org_id,
    #         )
    #     ).first()
    #     if not assignment:
    #         raise HTTPException(status_code=403, detail="You are not assigned to this exam's subject.")

    @staticmethod
    def _assert_can_view(
        session: Session, exam: ExamModel, current_user: CurrentUser, perm: P = P.EXAMS_VIEW
    ) -> None:
        """
        Whether the user may act on this exam under `perm`, by its reach:
        - all:      any exam in the organisation
        - assigned: exams they created, or in subjects they're assigned to
        - own:      only exams they created
        Drafts are only visible to people who can edit exams (so approvers never
        see work in progress). Students are handled separately (see get_by_id).
        """
        reach = current_user.reach(perm)
        if reach is None:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")

        if exam.status == ExamStatus.DRAFT and not current_user.can(P.EXAMS_EDIT):
            raise HTTPException(status_code=404, detail="Exam not found.")

        if reach == "all" or exam.created_by == current_user.id:
            return
        if reach == "own":
            raise HTTPException(status_code=403, detail="You can only do this on exams you created.")

        assignment = session.exec(
            select(SubjectAssignment).where(
                SubjectAssignment.subject_id == exam.subject_id,
                SubjectAssignment.assigned_to == current_user.id,
                SubjectAssignment.org_id == current_user.org_id,
            )
        ).first()
        if not assignment:
            raise HTTPException(status_code=403, detail="You are not assigned to this exam's subject.")



    @staticmethod
    def assert_assignable(session: Session, exam_id: UUID, current_user: CurrentUser) -> None:
        """
        Cheap pre-check, run before any network call:
        1. the exam exists in the caller's org (404 otherwise),
        2. the caller may access it: admins always, teachers if they created it or
        are assigned to its subject (403 otherwise),
        3. the exam is in a status that accepts assignments (400 otherwise).
        The access check comes before the status check so an unauthorised caller
        can't learn an exam's status.
        """
        exam: ExamModel = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_ASSIGN)
        if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
            raise HTTPException(
                status_code=400,
                detail="Students can only be assigned to approved or active exams.",
            )
        # Release the DB connection so it isn't held during the auth-service call.
        session.close()

    # --------------------------------------------------------
    # EXAM CRUD
    # --------------------------------------------------------

    @staticmethod
    def create(session: Session, payload: ExamCreate, current_user: CurrentUser) -> ExamModel:
        ScopeService.validate_bank(session, payload.subject_id, payload.bank_id, current_user.org_id)
        ScopeService.assert_bank_access(session, payload.bank_id, current_user)
        topic_ids: list[UUID] = ScopeService.validate_topics(
            session, payload.subject_id, payload.topic_ids, current_user.org_id
        )
        PeriodScope.validate(session, payload.period_id, current_user.org_id)
        exam = ExamModel(
            org_id=current_user.org_id,
            created_by=current_user.id,
            **payload.model_dump(exclude={"topic_ids"}),
        )
        session.add(exam)
        session.flush()
        ScopeService.set_exam_topics(session, exam.id, topic_ids)
        _log(session, exam.id, exam.org_id, current_user.id, "created")
        session.commit()
        session.refresh(exam)
        return exam

   

    # @staticmethod
    # def get_all(session: Session, current_user: CurrentUser) -> list[tuple[ExamModel, str | None]]:
    #     query = (
    #         select(ExamModel, SubjectModel.name)
    #         .join(SubjectModel, SubjectModel.id == ExamModel.subject_id, isouter=True)
    #         .where(ExamModel.org_id == current_user.org_id)
    #     )

    #     if current_user.role in (UserRole.TEACHER, UserRole.SUPERVISOR):
    #         assigned_subject_ids = select(SubjectAssignment.subject_id).where(
    #             SubjectAssignment.assigned_to == current_user.id,
    #             SubjectAssignment.org_id == current_user.org_id,
    #         )
    #         if current_user.role == UserRole.TEACHER:
    #             query = query.where(
    #                 or_(
    #                     ExamModel.created_by == current_user.id,
    #                     ExamModel.subject_id.in_(assigned_subject_ids),
    #                 )
    #             )
    #         else: # SUPERVISOR
    #              query = query.where(
    #                 ExamModel.subject_id.in_(assigned_subject_ids),
    #                 ExamModel.status != ExamStatus.DRAFT,
    #             )

    #     return session.exec(query).all()


    @staticmethod
    def _list_conditions(
        session: Session,
        current_user: CurrentUser,
        status: ExamStatus | None = None,
        subject_id: UUID | None = None,
        search: str | None = None,
        period_id: UUID | None = None,
    ) -> list[Any]:
        """Who may see what, plus the list filters (everything except the phase)."""
        conditions: list[Any] = [ExamModel.org_id == current_user.org_id]

        # Same rules as _assert_can_view, as query conditions.
        reach = current_user.reach(P.EXAMS_VIEW)
        if reach == "own":
            conditions.append(ExamModel.created_by == current_user.id)
        elif reach == "assigned":
            assigned_subject_ids = select(SubjectAssignment.subject_id).where(
                SubjectAssignment.assigned_to == current_user.id,
                SubjectAssignment.org_id == current_user.org_id,
            )
            conditions.append(
                or_(
                    ExamModel.created_by == current_user.id,
                    col(ExamModel.subject_id).in_(assigned_subject_ids),
                )
            )
        elif reach is None:
            conditions.append(false())
        if not current_user.can(P.EXAMS_EDIT):
            conditions.append(ExamModel.status != ExamStatus.DRAFT)

        if status is not None:
            conditions.append(ExamModel.status == status)
        if subject_id is not None:
            conditions.append(ExamModel.subject_id == subject_id)
        if period_id is not None:
            # A session also matches the exams set for its terms.
            period_ids: list[UUID] = PeriodScope.with_children(session, period_id, current_user.org_id)
            conditions.append(col(ExamModel.period_id).in_(period_ids))
        if search and search.strip():
            pattern: str = f"%{escape_like(search.strip())}%"
            conditions.append(col(ExamModel.title).ilike(pattern, escape="\\"))
        return conditions

    @staticmethod
    def _phase_condition(phase: ExamPhase, now: datetime) -> Any:
        """
        Where an exam is in its life, from its status and window:
        - drafts:   still being built (draft) or sent back (rejected)
        - upcoming: awaiting approval, or approved with a start still ahead
        - live:     approved/active and inside its window (no window = always open)
        - past:     closed/archived, or approved/active with its end behind it
        """
        running = col(ExamModel.status).in_([ExamStatus.APPROVED, ExamStatus.ACTIVE])
        started = or_(col(ExamModel.start_time).is_(None), col(ExamModel.start_time) <= now)
        not_ended = or_(col(ExamModel.end_time).is_(None), col(ExamModel.end_time) > now)
        if phase == ExamPhase.DRAFTS:
            return col(ExamModel.status).in_([ExamStatus.DRAFT, ExamStatus.REJECTED])
        if phase == ExamPhase.UPCOMING:
            return or_(
                ExamModel.status == ExamStatus.PENDING_APPROVAL,
                and_(running, col(ExamModel.start_time) > now),
            )
        if phase == ExamPhase.LIVE:
            return and_(running, started, not_ended)
        return or_(
            col(ExamModel.status).in_([ExamStatus.CLOSED, ExamStatus.ARCHIVED]),
            and_(running, col(ExamModel.end_time) <= now),
        )

    @staticmethod
    def get_all(
        session: Session,
        current_user: CurrentUser,
        status: ExamStatus | None = None,
        subject_id: UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
        period_id: UUID | None = None,
        phase: ExamPhase | None = None,
    ) -> tuple[list[tuple[ExamModel, str | None]], int]:
        conditions = ExamService._list_conditions(
            session, current_user, status=status, subject_id=subject_id, search=search, period_id=period_id
        )
        if phase is not None:
            conditions.append(ExamService._phase_condition(phase, datetime.now(timezone.utc)))

        total: int = session.exec(
            select(func.count()).select_from(ExamModel).where(*conditions)
        ).one()

        # Order to suit the phase: soonest first for what's coming / running,
        # most recent first for what's done. id keeps page boundaries stable.
        order: list[Any]
        if phase == ExamPhase.UPCOMING:
            order = [col(ExamModel.start_time).asc().nulls_last(), desc(col(ExamModel.updated_at))]
        elif phase == ExamPhase.LIVE:
            order = [col(ExamModel.end_time).asc().nulls_last(), desc(col(ExamModel.updated_at))]
        elif phase == ExamPhase.PAST:
            order = [col(ExamModel.end_time).desc().nulls_last(), desc(col(ExamModel.updated_at))]
        else:
            order = [desc(col(ExamModel.updated_at))]

        rows = session.exec(
            select(ExamModel, SubjectModel.name)
            .join(SubjectModel, SubjectModel.id == ExamModel.subject_id, isouter=True)
            .where(*conditions)
            .order_by(*order, col(ExamModel.id))
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        return [(exam, subject_name) for exam, subject_name in rows], total

    @staticmethod
    def phase_counts(
        session: Session,
        current_user: CurrentUser,
        subject_id: UUID | None = None,
        search: str | None = None,
        period_id: UUID | None = None,
    ) -> ExamPhaseCounts:
        """How many exams the caller can see in each phase, with the same filters as the list."""
        conditions = ExamService._list_conditions(
            session, current_user, subject_id=subject_id, search=search, period_id=period_id
        )
        now = datetime.now(timezone.utc)
        counts: dict[str, int] = {}
        for phase in ExamPhase:
            counts[phase.value] = session.exec(
                select(func.count()).select_from(ExamModel)
                .where(*conditions, ExamService._phase_condition(phase, now))
            ).one()
        counts["all"] = session.exec(select(func.count()).select_from(ExamModel).where(*conditions)).one()
        return ExamPhaseCounts(**counts)

   
    @staticmethod
    def get_by_id(session: Session, exam_id: UUID, current_user: CurrentUser) -> ExamModel:
        exam = session.exec(
            select(ExamModel).where(
                ExamModel.id == exam_id,
                ExamModel.org_id == current_user.org_id,
            )
        ).first()

        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found.")

        if current_user.role == UserRole.STUDENT:
            assignment = session.exec(
                select(ExamAssignment).where(
                    ExamAssignment.exam_id == exam_id,
                    ExamAssignment.student_id == current_user.id,
                )
            ).first()
            if not assignment:
                raise HTTPException(status_code=404, detail="Exam not found.")  # 404, not 403 — don't confirm existence
        else:
            ExamService._assert_can_view(session, exam, current_user)

        return exam


    # Exams whose attempts allowed can still change: approved, or live.
    ATTEMPTS_CHANGEABLE_STATUSES: tuple[ExamStatus, ...] = (ExamStatus.APPROVED, ExamStatus.ACTIVE)

    @staticmethod
    def update_max_attempts(
        session: Session, exam_id: UUID, payload: ExamMaxAttemptsUpdate, current_user: CurrentUser,
    ) -> ExamModel:
        """
        After approval only the attempts allowed may change (drafts use the
        normal edit). Lowering it never removes attempts already made; it only
        stops new ones.
        """
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_CHANGE_ATTEMPTS)
        if exam.status not in ExamService.ATTEMPTS_CHANGEABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Change attempts with Edit exam while the exam is a draft or sent back."
                    if exam.status in (ExamStatus.DRAFT, ExamStatus.REJECTED)
                    else "Attempts can only be changed while the exam is approved or live."
                ),
            )
        changed: bool = False
        old: int = exam.max_attempts or 1
        if payload.max_attempts is not None and payload.max_attempts != old:
            exam.max_attempts = payload.max_attempts
            _log(session, exam.id, exam.org_id, current_user.id, ExamAction.UPDATED,
                 {"field": "max_attempts", "from": old, "to": payload.max_attempts})
            changed = True
        old_show: bool = bool(exam.show_attempts_used)
        if payload.show_attempts_used is not None and payload.show_attempts_used != old_show:
            exam.show_attempts_used = payload.show_attempts_used
            _log(session, exam.id, exam.org_id, current_user.id, ExamAction.UPDATED,
                 {"field": "show_attempts_used", "from": old_show, "to": payload.show_attempts_used})
            changed = True
        old_extra: int = exam.extra_time_seconds or 0
        if payload.extra_time_seconds is not None and payload.extra_time_seconds != old_extra:
            exam.extra_time_seconds = payload.extra_time_seconds
            _log(session, exam.id, exam.org_id, current_user.id, ExamAction.UPDATED,
                 {"field": "extra_time_seconds", "from": old_extra, "to": payload.extra_time_seconds})
            changed = True
        if not changed:
            return exam
        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        session.commit()
        session.refresh(exam)
        return exam

    # Results can be shown or hidden from approval on, including after the exam closes.
    RESULTS_RELEASABLE_STATUSES: tuple[ExamStatus, ...] = (
        ExamStatus.APPROVED, ExamStatus.ACTIVE, ExamStatus.CLOSED,
    )

    @staticmethod
    def update_show_results(
        session: Session, exam_id: UUID, payload: ExamShowResultsUpdate, current_user: CurrentUser,
    ) -> ExamModel:
        """Release results to candidates (or hide them again) after approval."""
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_RELEASE_RESULTS)
        if exam.status not in ExamService.RESULTS_RELEASABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Set this with Edit exam while the exam is a draft or sent back."
                    if exam.status in (ExamStatus.DRAFT, ExamStatus.REJECTED)
                    else "Results can only be shown or hidden once the exam is approved, live or closed."
                ),
            )
        old: bool = bool(exam.show_result_immediately)
        if payload.show_result_immediately == old:
            return exam
        exam.show_result_immediately = payload.show_result_immediately
        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        _log(session, exam.id, exam.org_id, current_user.id, ExamAction.UPDATED,
             {"field": "show_result_immediately", "from": old, "to": payload.show_result_immediately})
        session.commit()
        session.refresh(exam)
        return exam

    @staticmethod
    def update(
        session: Session, exam_id: UUID,
        payload: ExamUpdate, current_user: CurrentUser
    ) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)

        data: dict[str, Any] = payload.model_dump(exclude_unset=True)
        new_topic_ids: list[UUID] | None = data.pop("topic_ids", None)
        if data.get("auto_mark_short_answers") is None:
            data.pop("auto_mark_short_answers", None)
        elif (
            data["auto_mark_short_answers"] != exam.auto_mark_short_answers
            and ExamStatus(exam.status) not in (ExamStatus.DRAFT, ExamStatus.REJECTED)
        ):
            # Students may already have answers marked one way.
            raise HTTPException(
                status_code=409,
                detail="How short answers are marked can only be changed while the exam is a draft.",
            )
        if "bank_id" in data and data["bank_id"] != exam.bank_id:
            ScopeService.validate_bank(session, exam.subject_id, data["bank_id"], current_user.org_id)
            ScopeService.assert_bank_access(session, data["bank_id"], current_user)
        if "period_id" in data and data["period_id"] != exam.period_id:
            PeriodScope.validate(session, data["period_id"], current_user.org_id)
        if new_topic_ids is not None:
            new_topic_ids = ScopeService.validate_topics(
                session, exam.subject_id, new_topic_ids, current_user.org_id
            )
            ScopeService.set_exam_topics(session, exam.id, new_topic_ids)

        for key, value in data.items():
            setattr(exam, key, value)

        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        _log(session, exam.id, exam.org_id, current_user.id, "updated")
        session.commit()
        session.refresh(exam)
        return exam


    @staticmethod
    def delete(session: Session, exam_id: UUID, current_user: CurrentUser) -> ExamDeleteResponse:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)

        has_attempts = session.exec(
            select(AttemptModel.id).where(AttemptModel.exam_id == exam_id).limit(1)
        ).first()

        if has_attempts:
            # Preserve history — students have already engaged with this exam
            exam.status = ExamStatus.ARCHIVED
            exam.updated_at = datetime.now(timezone.utc)
            session.add(exam)
            _log(session, exam_id, current_user.org_id, current_user.id, "exam_archived", None)
            session.commit()
            return ExamDeleteResponse(
                detail="Exam archived — attempts exist and were preserved.",
                exam_id=exam_id,
                action="archived",
            )

        # No attempts — safe to fully remove, including exam-scoped child rows
        session.exec(sql_delete(ExamItem).where(ExamItem.exam_id == exam_id))
        session.exec(sql_delete(ExamSection).where(ExamSection.exam_id == exam_id))
        session.exec(sql_delete(ExamAssignment).where(ExamAssignment.exam_id == exam_id))
        session.exec(sql_delete(ExamAuditLog).where(ExamAuditLog.exam_id == exam_id))
        session.delete(exam)
        session.commit()
        _log(session, exam_id, current_user.org_id, current_user.id, "deleted", {"exam_title": exam.title})

        return ExamDeleteResponse(
            detail="Exam permanently deleted.",
            exam_id=exam_id,
            action="deleted",
        )

    # --------------------------------------------------------
    # APPROVAL FLOW
    # --------------------------------------------------------

    @staticmethod
    def submit_for_approval(session: Session, exam_id: UUID, current_user: CurrentUser) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)

        if exam.status not in (ExamStatus.DRAFT, ExamStatus.REJECTED):
            raise HTTPException(status_code=400, detail="Only draft or rejected exams can be submitted.")

        if current_user.reach(P.EXAMS_SUBMIT) == "own" and exam.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="You can only submit your own exams.")
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_SUBMIT)

        # Must have at least one item
        # item_count = len(session.exec(
        #     select(ExamItem).where(ExamItem.exam_id == exam_id)
        # ).all())

        item_count: int = session.exec(
            select(func.count()).select_from(ExamItem).where(ExamItem.exam_id == exam_id)
        ).one()

        if item_count == 0:
            raise HTTPException(status_code=400, detail="Exam must have at least one question before submission.")

        exam.status = ExamStatus.PENDING_APPROVAL
        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        _log(session, exam.id, exam.org_id, current_user.id, "submitted_for_approval")
        session.commit()
        session.refresh(exam)
        return exam

    @staticmethod
    def process_approval(
        session: Session, exam_id: UUID,
        payload: ApprovalAction, current_user: CurrentUser
    ) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_APPROVE)

        if exam.status != ExamStatus.PENDING_APPROVAL:
            raise HTTPException(status_code=400, detail="Only pending exams can be approved or rejected.")

        if payload.action == ExamStatus.APPROVED:
            exam.status = ExamStatus.APPROVED
            exam.approved_by = current_user.id
            exam.rejection_reason = None
            _log(session, exam.id, exam.org_id, current_user.id, "approved")
        else:
            exam.status = ExamStatus.REJECTED
            exam.rejected_by = current_user.id
            exam.rejection_reason = payload.rejection_reason
            _log(session, exam.id, exam.org_id, current_user.id, "rejected",
                 {"reason": payload.rejection_reason})

        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        session.commit()
        session.refresh(exam)
        return exam

    # --------------------------------------------------------
    # SECTIONS
    # --------------------------------------------------------

    @staticmethod
    def add_section(
        session: Session, exam_id: UUID,
        payload: ExamSectionCreate, current_user: CurrentUser
    ) -> ExamSection:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)

        section = ExamSection(
            exam_id=exam_id,
            org_id=current_user.org_id,
            **payload.model_dump(),
        )
        session.add(section)
        session.commit()
        session.refresh(section)
        return section

    
    @staticmethod
    def get_sections(session: Session, exam_id: UUID, current_user: CurrentUser) -> list[ExamSection]:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)
        return session.exec(
            select(ExamSection).where(ExamSection.exam_id == exam_id).order_by(ExamSection.order)
        ).all()

    # --------------------------------------------------------
    # ITEMS
    # --------------------------------------------------------

    @staticmethod
    def add_items(
        session: Session, exam_id: UUID,
        payload: ExamItemAdd, current_user: CurrentUser
    ) -> list[ExamItem]:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)

        existing = session.exec(select(ExamItem).where(ExamItem.exam_id == exam_id)).all()
        next_order: int = len(existing)
        total_marks = sum(i.marks for i in existing)
        existing_item_ids: set[UUID] = {i.item_id for i in existing}

        # Only questions from this exam's subject, in the caller's organisation,
        # and not archived. Ones already in the exam are skipped below, so they
        # aren't re-checked.
        new_ids: list[UUID] = [i for i in dict.fromkeys(payload.item_ids) if i not in existing_item_ids]
        # The exam keeps its own copy of each question's marks (so editing the
        # question later doesn't change an exam that has run).
        item_marks: dict[UUID, tuple[float, float]] = {}
        if new_ids:
            # ...and only questions in banks the caller can use (or in no bank).
            visible = ScopeService.visible_items_condition(ScopeService.allowed_bank_ids(session, current_user))
            for item_id, marks, negative_marks in session.exec(
                select(ItemModel.id, ItemModel.marks, ItemModel.negative_marks).where(
                    col(ItemModel.id).in_(new_ids),
                    ItemModel.org_id == current_user.org_id,
                    ItemModel.subject_id == exam.subject_id,
                    ItemModel.status != ItemStatus.ARCHIVED,
                    *([visible] if visible is not None else []),
                )
            ).all():
                item_marks[item_id] = (float(marks), float(negative_marks or 0.0))
            usable: set[UUID] = set(item_marks)
            invalid: list[str] = [str(i) for i in new_ids if i not in usable]
            if invalid:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "Some questions can't be added: they aren't in this exam's subject, "
                            "are archived, or are in a bank you haven't been given."
                        ),
                        "item_ids": invalid,
                    },
                )

        added: list[ExamItem] = []
        for item_id in dict.fromkeys(payload.item_ids):
            if item_id in existing_item_ids:
                continue
            marks, negative_marks = item_marks.get(item_id, (1.0, 0.0))
            exam_item = ExamItem(
                exam_id=exam_id, section_id=payload.section_id, item_id=item_id,
                org_id=current_user.org_id, order=next_order,
                marks=marks, negative_marks=negative_marks,
            )
            session.add(exam_item)
            added.append(exam_item)
            next_order += 1
            total_marks += exam_item.marks

        exam.total_marks = total_marks
        session.add(exam)
        session.commit()
        return added   # delete the per-item refresh loop

        # Get current max order
        # existing = session.exec(
        #     select(ExamItem).where(ExamItem.exam_id == exam_id)
        # ).all()
        # next_order = len(existing)
        # total_marks = sum(i.marks for i in existing)    # existing marks

        # added = []
        # for item_id in payload.item_ids:
        #     # Prevent duplicate items in same exam
        #     duplicate = session.exec(
        #         select(ExamItem).where(
        #             ExamItem.exam_id == exam_id,
        #             ExamItem.item_id == item_id,
        #         )
        #     ).first()
        #     if duplicate:
        #         continue

        #     exam_item = ExamItem(
        #         exam_id=exam_id,
        #         section_id=payload.section_id,
        #         item_id=item_id,
        #         org_id=current_user.org_id,
        #         order=next_order,
        #     )
        #     session.add(exam_item)
        #     added.append(exam_item)
        #     next_order += 1
        #     total_marks += exam_item.marks

        # # Update total marks on exam
        # exam.total_marks = total_marks  # + sum(i.marks for i in added)
        # session.add(exam)
        # session.commit()
        # for item in added:
        #     session.refresh(item)
        # return added

    @staticmethod
    def add_items_from_bank(
        session: Session, exam_id: UUID,
        payload: ExamItemsFromBank, current_user: CurrentUser
    ) -> ExamItemsFromBankResult:
        """
        Adds active questions from the exam's subject that match a bank and/or
        topics (any of), optionally a difficulty / type, skipping ones already
        in the exam. With `count`, adds that many (randomly drawn by default).
        """
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)
        if payload.bank_id is not None:
            ScopeService.get_bank(session, exam.subject_id, payload.bank_id, current_user.org_id)
            ScopeService.assert_bank_access(session, payload.bank_id, current_user)
        topic_ids: list[UUID] = ScopeService.validate_topics(
            session, exam.subject_id, payload.topic_ids, current_user.org_id
        )

        conditions: list[Any] = [
            ItemModel.subject_id == exam.subject_id,
            ItemModel.org_id == current_user.org_id,
            ItemModel.status == ItemStatus.ACTIVE,
            ~exists().where(ExamItem.exam_id == exam_id, ExamItem.item_id == ItemModel.id),
        ]
        visible = ScopeService.visible_items_condition(ScopeService.allowed_bank_ids(session, current_user))
        if visible is not None:
            conditions.append(visible)
        if payload.bank_id is not None:
            conditions.append(ItemModel.bank_id == payload.bank_id)
        if topic_ids:
            conditions.append(
                exists().where(ItemTopic.item_id == ItemModel.id, col(ItemTopic.topic_id).in_(topic_ids))
            )
        if payload.period_id is not None:
            period_ids: list[UUID] = PeriodScope.with_children(session, payload.period_id, current_user.org_id)
            conditions.append(col(ItemModel.period_id).in_(period_ids))
        difficulty_key: str | None = DifficultyScope.filter_key(session, current_user.org_id, payload.difficulty)
        if difficulty_key is not None:
            conditions.append(ItemModel.difficulty == difficulty_key)
        if payload.item_type is not None:
            conditions.append(ItemModel.item_type == payload.item_type)

        matched: int = session.exec(select(func.count()).select_from(ItemModel).where(*conditions)).one()

        query = select(ItemModel.id).where(*conditions)
        if payload.count is not None and payload.random:
            query = query.order_by(func.random())
        else:
            query = query.order_by(col(ItemModel.created_at))
        if payload.count is not None:
            query = query.limit(payload.count)
        item_ids: list[UUID] = list(session.exec(query).all())

        if not item_ids:
            return ExamItemsFromBankResult(matched=matched, added=0, item_ids=[])

        added: list[ExamItem] = ExamService.add_items(
            session, exam_id, ExamItemAdd(item_ids=item_ids, section_id=payload.section_id), current_user
        )
        return ExamItemsFromBankResult(matched=matched, added=len(added), item_ids=[a.item_id for a in added])

    @staticmethod
    def to_reads(
        session: Session, exams: list[ExamModel], subject_names: dict[UUID, str | None] | None = None
    ) -> list[ExamRead]:
        """ExamRead for each exam with its topic ids (one query for all)."""
        ids: list[UUID] = [e.id for e in exams]
        topics: dict[UUID, list[UUID]] = ScopeService.topic_ids_by_exam(session, ids)
        names: dict[UUID, str | None] = subject_names or {}
        item_counts: dict[UUID, int] = {}
        written: dict[UUID, int] = {}
        short: dict[UUID, int] = {}
        assigned: dict[UUID, int] = {}
        submitted: dict[UUID, int] = {}
        if ids:
            # One grouped query each, so list pages don't do a query per exam.
            for exam_id, n in session.exec(
                select(ExamItem.exam_id, func.count()).where(col(ExamItem.exam_id).in_(ids))
                .group_by(col(ExamItem.exam_id))
            ).all():
                item_counts[exam_id] = int(n)
            for exam_id, item_type, n in session.exec(
                select(ExamItem.exam_id, ItemModel.item_type, func.count())
                .join(ItemModel, col(ItemModel.id) == col(ExamItem.item_id))
                .where(col(ExamItem.exam_id).in_(ids), col(ItemModel.item_type).in_([ItemType.SHORT_ANSWER, ItemType.ESSAY]))
                .group_by(col(ExamItem.exam_id), col(ItemModel.item_type))
            ).all():
                written[exam_id] = written.get(exam_id, 0) + int(n)
                if str(getattr(item_type, "value", item_type)).lower() == "short_answer":
                    short[exam_id] = short.get(exam_id, 0) + int(n)
            for exam_id, n_all, n_done in session.exec(
                select(
                    ExamAssignment.exam_id,
                    func.count(),
                    func.count().filter(ExamAssignment.status == AssignmentStatus.SUBMITTED),
                ).where(col(ExamAssignment.exam_id).in_(ids)).group_by(col(ExamAssignment.exam_id))
            ).all():
                assigned[exam_id] = int(n_all)
                submitted[exam_id] = int(n_done)
        return [
            ExamRead.model_validate({
                **e.model_dump(),
                "topic_ids": topics.get(e.id, []),
                "item_count": item_counts.get(e.id, 0),
                "written_count": written.get(e.id, 0),
                "short_answer_count": short.get(e.id, 0),
                "assigned_count": assigned.get(e.id, 0),
                "submitted_count": submitted.get(e.id, 0),
                **({"subject_name": names[e.id]} if e.id in names else {}),
            })
            for e in exams
        ]

    @staticmethod
    def remove_item(
        session: Session, exam_id: UUID,
        exam_item_id: UUID, current_user: CurrentUser
    ):
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)

        exam_item = session.exec(
            select(ExamItem).where(
                ExamItem.id == exam_item_id,
                ExamItem.exam_id == exam_id,
            )
        ).first()
        if not exam_item:
            raise HTTPException(status_code=404, detail="Item not found in this exam.")

        exam.total_marks = max(0, exam.total_marks - exam_item.marks)
        session.delete(exam_item)
        session.add(exam)
        session.commit()

    

    @staticmethod
    def get_items(session: Session, exam_id: UUID, current_user: CurrentUser) -> list[ExamItem]:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)
        return session.exec(
            select(ExamItem).where(ExamItem.exam_id == exam_id).order_by(ExamItem.order)
        ).all()

    # --------------------------------------------------------
    # STUDENT ASSIGNMENT
    # --------------------------------------------------------

    @staticmethod
    def _bulk_assign(
        session: Session,
        exam_id: UUID,
        student_ids: list[UUID],
        org_id: UUID,
        assigned_by: UUID,
        scheduled_at: datetime | None,
        cohort_id: UUID | None = None,
    ) -> int:
        """
        Insert assignments, silently skipping students already assigned.
        Returns how many rows were actually created. Does not commit.
        Race-safe: relies on uq_exam_assignments_exam_student.
        """
        unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
        created: int = 0

        for start in range(0, len(unique_ids), _ASSIGN_CHUNK_SIZE):
            chunk: list[UUID] = unique_ids[start:start + _ASSIGN_CHUNK_SIZE]

            # Model instances are transient: never added to the session, only used
            # to build the rows. id, status and created_at come from the model defaults.
            models: list[ExamAssignment] = [
                ExamAssignment(
                    exam_id=exam_id,
                    student_id=sid,
                    org_id=org_id,
                    assigned_by=assigned_by,
                    cohort_id=cohort_id,
                    scheduled_at=scheduled_at,
                )
                for sid in chunk
            ]
            rows: list[dict[str, Any]] = [m.model_dump() for m in models]

            stmt = (
                pg_insert(ExamAssignment)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["exam_id", "student_id"])
                .returning(ExamAssignment.id)
            )
            created += len(session.execute(stmt).all())

        return created


    @staticmethod
    def _load_assignments(
        session: Session, exam_id: UUID, student_ids: list[UUID]
    ) -> list[ExamAssignment]:
        """Fresh load after commit: one query per chunk, in the caller's order."""
        unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
        by_student: dict[UUID, ExamAssignment] = {}

        for start in range(0, len(unique_ids), _ASSIGN_CHUNK_SIZE):
            chunk: list[UUID] = unique_ids[start:start + _ASSIGN_CHUNK_SIZE]
            for a in session.exec(
                select(ExamAssignment).where(
                    ExamAssignment.exam_id == exam_id,
                    col(ExamAssignment.student_id).in_(chunk),
                )
            ).all():
                by_student[a.student_id] = a

        return [by_student[s] for s in unique_ids if s in by_student]


    @staticmethod
    def assign_students(
        session: Session, exam_id: UUID,
        payload: AssignStudentsRequest, current_user: CurrentUser,
    ) -> list[ExamAssignment]:
        exam: ExamModel = ExamService._get_exam(session, exam_id, current_user.org_id)
        if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
            raise HTTPException(status_code=400, detail="Students can only be assigned to approved or active exams.")

        unique_ids: list[UUID] = list(dict.fromkeys(payload.student_ids))
        actor_id: UUID = UUID(str(current_user.id))

        created: int = ExamService._bulk_assign(
            session, exam_id, unique_ids, current_user.org_id, actor_id, payload.scheduled_at,
        )
        _log(session, exam_id, exam.org_id, actor_id, "students_assigned",
            {"requested": len(unique_ids), "created": created})
        session.commit()  # assignments + audit log together

        return ExamService._load_assignments(session, exam_id, unique_ids)


    # @staticmethod
    # def assign_students(
    #     session: Session, exam_id: UUID,
    #     payload: AssignStudentsRequest, current_user: CurrentUser
    # ) -> list[ExamAssignment]:
    #     exam = ExamService._get_exam(session, exam_id, current_user.org_id)

    #     if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
    #         raise HTTPException(
    #             status_code=400,
    #             detail="Students can only be assigned to approved or active exams."
    #         )

    #     assignments = []
    #     for student_id in payload.student_ids:
    #         existing = session.exec(
    #             select(ExamAssignment).where(
    #                 ExamAssignment.exam_id == exam_id,
    #                 ExamAssignment.student_id == student_id,
    #             )
    #         ).first()
    #         if existing:
    #             assignments.append(existing)
    #             continue

    #         assignment = ExamAssignment(
    #             exam_id=exam_id,
    #             student_id=student_id,
    #             org_id=current_user.org_id,
    #             assigned_by=current_user.id,
    #             scheduled_at=payload.scheduled_at,
    #         )
    #         session.add(assignment)
    #         assignments.append(assignment)

    #     session.commit()
    #     _log(session, exam_id, exam.org_id, current_user.id, "students_assigned",
    #          {"count": len(payload.student_ids)})
    #     session.commit()
    #     return assignments

    

    @staticmethod
    def get_assignments(session: Session, exam_id: UUID, current_user: CurrentUser) -> list[ExamAssignment]:
        return list(session.exec(ExamService.assignments_stmt(session, exam_id, current_user)).all())

    @staticmethod
    def assignments_stmt(session: Session, exam_id: UUID, current_user: CurrentUser) -> "SelectOfScalar[ExamAssignment]":
        """The exam's assignments, oldest first (checks access)."""
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user, P.EXAMS_ASSIGN)
        return (
            select(ExamAssignment).where(ExamAssignment.exam_id == exam_id)
            .order_by(col(ExamAssignment.created_at), col(ExamAssignment.id))
        )

    @staticmethod
    def get_audit_log(
        session: Session, exam_id: UUID, current_user: CurrentUser
    ) -> list[ExamAuditLog]:
        ExamService._get_exam(session, exam_id, current_user.org_id)
        return session.exec(
            select(ExamAuditLog).where(
                ExamAuditLog.exam_id == exam_id
            ).order_by(ExamAuditLog.created_at)
        ).all()

    @staticmethod
    def audit_stmt(session: Session, exam_id: UUID, current_user: CurrentUser) -> "SelectOfScalar[ExamAuditLog]":
        """The exam's activity, newest first (for paging)."""
        ExamService._get_exam(session, exam_id, current_user.org_id)
        return (
            select(ExamAuditLog).where(ExamAuditLog.exam_id == exam_id)
            .order_by(col(ExamAuditLog.created_at).desc(), col(ExamAuditLog.id))
        )
    


    @staticmethod
    def assign_cohort(
        session: Session, exam_id: UUID, cohort_id: UUID,
        scheduled_at: datetime | None, current_user: CurrentUser,
        student_ids: list[UUID],
    ) -> list[ExamAssignment]:
        exam: ExamModel = ExamService._get_exam(session, exam_id, current_user.org_id)
        if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
            raise HTTPException(status_code=400, detail="Students can only be assigned to approved or active exams.")

        unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
        actor_id: UUID = UUID(str(current_user.id))

        created: int = ExamService._bulk_assign(
            session, exam_id, unique_ids, current_user.org_id, actor_id, scheduled_at,
            cohort_id=cohort_id,
        )
        session.commit()

        try:  # an audit failure must not undo the assignments
            _log(session, exam_id, current_user.org_id, actor_id, "assigned_to_cohort",
                {"cohort_id": str(cohort_id), "requested": len(unique_ids), "created": created})
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to write audit log for assigned_to_cohort on exam_id=%s cohort_id=%s",
                exam_id, cohort_id,
            )

        return ExamService._load_assignments(session, exam_id, unique_ids)





    @staticmethod
    def get_sections_with_items_internal(session: Session, exam_id: UUID) -> list[ExamSectionInternalRead]:
        sections = session.exec(
            select(ExamSection)
            .where(ExamSection.exam_id == exam_id)
            .order_by(ExamSection.order)
        ).all()

        all_items = session.exec(
            select(ExamItem)
            .where(ExamItem.exam_id == exam_id)
            .order_by(ExamItem.order)
        ).all()

        items_by_section: dict[UUID | None, list[ExamItem]] = {}
        for item in all_items:
            items_by_section.setdefault(item.section_id, []).append(item)

        result = []
        for section in sections:
            section_items = items_by_section.pop(section.id, [])
            result.append(ExamSectionInternalRead(
                id=section.id,
                title=section.title,
                order=section.order,
                items=[
                    ExamItemInternalRead(id=ei.id, item_id=ei.item_id, order=ei.order, marks=ei.marks)
                    for ei in section_items
                ],
            ))

        unsectioned = items_by_section.pop(None, [])
        if unsectioned:
            result.append(ExamSectionInternalRead(
                id=exam_id,
                title="General",
                order=len(result),
                items=[
                    ExamItemInternalRead(id=ei.id, item_id=ei.item_id, order=ei.order, marks=ei.marks)
                    for ei in unsectioned
                ],
            ))

        return result



    @staticmethod
    def get_my_assignments(
        session: Session,
        current_user: CurrentUser,
    ) -> list[MyAssignmentRead]:

        assignments = session.exec(
            select(ExamAssignment, ExamModel)
            .join(
                ExamModel,
                ExamAssignment.exam_id == ExamModel.id,
            )
            .where(
                ExamAssignment.student_id == current_user.id,
                ExamAssignment.org_id == current_user.org_id,
            )
            .order_by(
                ExamModel.start_time.desc().nulls_last()
            )
        ).all()

        now = datetime.now(timezone.utc)

        # The student's attempts on these exams, in one query.
        attempts_by_exam: dict[UUID, list[AttemptModel]] = {}
        exam_ids: list[UUID] = list({exam.id for _, exam in assignments})
        if exam_ids:
            for attempt in session.exec(
                select(AttemptModel).where(
                    AttemptModel.student_id == current_user.id,
                    col(AttemptModel.exam_id).in_(exam_ids),
                )
            ).all():
                attempts_by_exam.setdefault(attempt.exam_id, []).append(attempt)
        closed: tuple[AttemptStatus, ...] = (AttemptStatus.SUBMITTED, AttemptStatus.SCORED, AttemptStatus.TERMINATED)

        def _when(a: AttemptModel) -> datetime:
            when = a.submitted_at or a.started_at
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

        results = []

        for assignment, exam in assignments:
            # Resolve the actual window for this specific assignment.
            # Assignment-level scheduling should override the exam-level
            # window where applicable.
            effective_start, effective_end = (
                ExamService.get_effective_window(
                    exam,
                    assignment,
                )
            )

            can_attempt = (
                (effective_start is None or now >= effective_start)
                and
                (effective_end is None or now <= effective_end)
            )

            mine: list[AttemptModel] = attempts_by_exam.get(exam.id, [])
            resumable: list[AttemptModel] = [
                a for a in mine if a.status in (AttemptStatus.STARTED, AttemptStatus.RESET)
            ]
            finished: list[AttemptModel] = [a for a in mine if a.status in closed]
            attempts_used: int = sum(1 for a in mine if a.status != AttemptStatus.RESET)
            max_attempts: int = exam.max_attempts or 1
            in_progress: AttemptModel | None = (
                # A started attempt first; else one staff reset for a fresh start.
                next((a for a in resumable if a.status == AttemptStatus.STARTED), None)
                or (max(resumable, key=_when) if resumable else None)
            )
            latest: AttemptModel | None = max(finished, key=_when) if finished else None

            results.append(
                MyAssignmentRead(
                    # Counts only when the exam shows them to candidates.
                    max_attempts=max_attempts if exam.show_attempts_used else None,
                    attempts_used=attempts_used if exam.show_attempts_used else None,
                    show_attempts_used=bool(exam.show_attempts_used),
                    in_progress_attempt_id=in_progress.id if in_progress else None,
                    restart=in_progress is not None and in_progress.status == AttemptStatus.RESET,
                    latest_attempt_id=latest.id if latest else None,
                    can_retake=(
                        latest is not None and in_progress is None
                        and attempts_used < max_attempts and can_attempt
                    ),
                    show_result=bool(exam.show_result_immediately),
                    allow_review=bool(exam.allow_review),
                    assignment_id=assignment.id,
                    exam_id=exam.id,
                    exam_title=exam.title,
                    status=assignment.status,
                    scheduled_at=assignment.scheduled_at,
                    duration_minutes=exam.duration_minutes,
                    extra_time_seconds=exam.extra_time_seconds or 0,
                    start_time=exam.start_time,
                    end_time=exam.end_time,

                    effective_start=effective_start,
                    effective_end=effective_end,
                    can_attempt=can_attempt,

                    has_attempted=bool(mine),
                )
            )

        return results




    @staticmethod
    def update_status(
        session: Session,
        exam_id: UUID,
        new_status: ExamStatus,
        reason: str | None,
        current_user: CurrentUser,
    ) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)

        if exam.status == new_status:
            return exam  # no-op

        ExamService._assert_valid_status_override(exam.status, new_status)

        old_status = exam.status
        exam.status = new_status
        exam.updated_at = datetime.now(timezone.utc)

        if new_status == ExamStatus.REJECTED and reason:
            exam.rejection_reason = reason
        if new_status == ExamStatus.APPROVED:
            exam.approved_by = current_user.id
        if new_status == ExamStatus.REJECTED:
            exam.rejected_by = current_user.id

        session.add(exam)

        _log(
            session, exam_id, current_user.org_id, current_user.id,
            "exam_status_override",
            {"from": old_status, "to": new_status, "reason": reason},
        )

        session.commit()
        session.refresh(exam)
        return exam


    @staticmethod
    def _assert_valid_status_override(current_status: ExamStatus, new_status: ExamStatus) -> None:
        """
        Super-admin/admin override — deliberately more permissive than the normal
        submit → approve/reject workflow, since this exists specifically for
        fixing mistakes (e.g. an exam wrongly approved, or one that needs to
        go back to draft for edits after teachers already added items).

        Still blocks nonsensical destinations: nothing should be manually set
        to ARCHIVED here — that's reserved for delete()'s attempt-preservation
        path, since ARCHIVED has a specific meaning tied to attempt history.
        """
        if new_status == ExamStatus.ARCHIVED:
            raise HTTPException(
                status_code=400,
                detail="Exams can only be archived via the delete endpoint, not this override.",
            )

        if current_status == ExamStatus.ARCHIVED:
            raise HTTPException(
                status_code=400,
                detail="Archived exams cannot have their status changed. They are a terminal state.",
            )


    @staticmethod
    def get_effective_window(exam: ExamModel, assignment: ExamAssignment) -> tuple[datetime | None, datetime | None]:
        """
        Single source of truth for "when can this specific student attempt this exam."
        A reset with a new scheduled time overrides the exam's global window for
        that assignment only — everyone else still uses the exam-level window.
        """
        effective_start = assignment.scheduled_at or exam.start_time
        effective_end = assignment.deadline_override or exam.end_time
        return effective_start, effective_end





    @staticmethod
    def get_items_with_content(
        session: Session, exam_id: UUID, current_user: CurrentUser
    ) -> list[tuple[ExamItem, ItemModel]]:
        return list(session.exec(ExamService.items_with_content_stmt(session, exam_id, current_user)).all())

    @staticmethod
    def items_with_content_stmt(session: Session, exam_id: UUID, current_user: CurrentUser) -> "Select[tuple[ExamItem, ItemModel]]":
        """The exam's questions with their content, in exam order (checks access)."""
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)
        # Section by section (in section order, unsectioned last), then question order,
        # so a page reads the same as the exam.
        return (
            select(ExamItem, ItemModel)
            .join(ItemModel, ItemModel.id == ExamItem.item_id)
            .outerjoin(ExamSection, col(ExamSection.id) == col(ExamItem.section_id))
            .where(
                ExamItem.exam_id == exam_id,
                ItemModel.org_id == current_user.org_id,
            )
            .order_by(col(ExamSection.order).asc().nulls_last(), ExamItem.order, col(ExamItem.id))
        )