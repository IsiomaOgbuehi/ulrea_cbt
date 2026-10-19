import logging
from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException, logger
from sqlmodel import Session, select, delete as sql_delete, col, func
from sqlalchemy import or_, and_, desc
from typing import Any
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cbt_service.services.item.utils.search import escape_like
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectAssignment, SubjectModel
from cbt_service.database.models.attempt import AttemptModel
from cbt_service.database.models.exam import (
    ExamModel, ExamSection, ExamItem, ExamAssignment, ExamAuditLog
)
from cbt_service.database.models.enums.exam_enum import ExamStatus, AssignmentStatus
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.schemas.exam_schemas import (
    ExamCreate, ExamDeleteResponse, ExamItemInternalRead, ExamSectionInternalRead, ExamUpdate, ExamSectionCreate,
    ExamItemAdd, AssignStudentsRequest, ApprovalAction, CurrentUser, MyAssignmentRead
)


def _log(session: Session, exam_id: UUID, org_id: UUID, actor_id: UUID, action: str, metadata: dict = None):
    entry = ExamAuditLog(
        exam_id=exam_id,
        org_id=org_id,
        actor_id=actor_id,
        action=action,
        metadata=metadata,
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
        if current_user.role == UserRole.SUPERVISOR:
            raise HTTPException(status_code=403, detail="Supervisors cannot edit exams.")
        if exam.status not in (ExamStatus.DRAFT, ExamStatus.REJECTED):
            raise HTTPException(status_code=400, detail=f"Exam cannot be edited in '{exam.status}' status.")
        ExamService._assert_can_view(session, exam, current_user)


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
    def _assert_can_view(session: Session, exam: ExamModel, current_user: CurrentUser) -> None:
        """
        Admin/super_admin: unrestricted.
        Teacher: creator, or assigned to the exam's subject.
        Supervisor: assigned to the exam's subject AND exam is past DRAFT.
        Students are handled separately via ExamAssignment (see get_by_id).
        """
        if current_user.role in (UserRole.SUPER_ADMIN, UserRole.ADMIN):
            return

        if current_user.role == UserRole.SUPERVISOR and exam.status == ExamStatus.DRAFT:
            raise HTTPException(status_code=404, detail="Exam not found.")

        if current_user.role == UserRole.TEACHER and exam.created_by == current_user.id:
            return

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
        ExamService._assert_can_view(session, exam, current_user)
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
        exam = ExamModel(
            org_id=current_user.org_id,
            created_by=current_user.id,
            **payload.model_dump(),
        )
        session.add(exam)
        session.flush()
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
    def get_all(
        session: Session,
        current_user: CurrentUser,
        status: ExamStatus | None = None,
        subject_id: UUID | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[ExamModel, str | None]], int]:
        conditions: list = [ExamModel.org_id == current_user.org_id]

        if current_user.role in (UserRole.TEACHER, UserRole.SUPERVISOR):
            assigned_subject_ids = select(SubjectAssignment.subject_id).where(
                SubjectAssignment.assigned_to == current_user.id,
                SubjectAssignment.org_id == current_user.org_id,
            )
            if current_user.role == UserRole.TEACHER:
                conditions.append(
                    or_(
                        ExamModel.created_by == current_user.id,
                        col(ExamModel.subject_id).in_(assigned_subject_ids),
                    )
                )
            else:  # SUPERVISOR: assigned subjects only, never drafts
                conditions.append(col(ExamModel.subject_id).in_(assigned_subject_ids))
                conditions.append(ExamModel.status != ExamStatus.DRAFT)

        if status is not None:
            conditions.append(ExamModel.status == status)
        if subject_id is not None:
            conditions.append(ExamModel.subject_id == subject_id)
        if search and search.strip():
            pattern: str = f"%{escape_like(search.strip())}%"
            conditions.append(col(ExamModel.title).ilike(pattern, escape="\\"))

        total: int = session.exec(
            select(func.count()).select_from(ExamModel).where(*conditions)
        ).one()

        rows = session.exec(
            select(ExamModel, SubjectModel.name)
            .join(SubjectModel, SubjectModel.id == ExamModel.subject_id, isouter=True)
            .where(*conditions)
            .order_by(desc(col(ExamModel.updated_at)), col(ExamModel.id))  # id keeps page boundaries stable
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()

        return [(exam, subject_name) for exam, subject_name in rows], total

   
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


    @staticmethod
    def update(
        session: Session, exam_id: UUID,
        payload: ExamUpdate, current_user: CurrentUser
    ) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(session, exam, current_user)

        for key, value in payload.model_dump(exclude_unset=True).items():
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

        if current_user.role == UserRole.TEACHER and exam.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="You can only submit your own exams.")

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
        ExamService._assert_can_view(session, exam, current_user)   # <- add (no-op for admins)

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

        added: list[ExamItem] = []
        for item_id in dict.fromkeys(payload.item_ids):
            if item_id in existing_item_ids:
                continue
            exam_item = ExamItem(
                exam_id=exam_id, section_id=payload.section_id, item_id=item_id,
                org_id=current_user.org_id, order=next_order,
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
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)
        return session.exec(
            select(ExamAssignment).where(ExamAssignment.exam_id == exam_id)
        ).all()

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

            results.append(
                MyAssignmentRead(
                    assignment_id=assignment.id,
                    exam_id=exam.id,
                    exam_title=exam.title,
                    status=assignment.status,
                    scheduled_at=assignment.scheduled_at,
                    duration_minutes=exam.duration_minutes,
                    start_time=exam.start_time,
                    end_time=exam.end_time,

                    effective_start=effective_start,
                    effective_end=effective_end,
                    can_attempt=can_attempt,

                    has_attempted=assignment.status == AssignmentStatus.ASSIGNED,
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
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_view(session, exam, current_user)

        rows = session.exec(
            select(ExamItem, ItemModel)
            .join(ItemModel, ItemModel.id == ExamItem.item_id)
            .where(
                ExamItem.exam_id == exam_id,
                ItemModel.org_id == current_user.org_id,
            )
            .order_by(ExamItem.order)
        ).all()
        return list(rows)