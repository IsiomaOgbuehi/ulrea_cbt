from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException, logger
from sqlmodel import Session, select

from cbt_service.database.models.exam import (
    ExamModel, ExamSection, ExamItem, ExamAssignment, ExamAuditLog
)
from cbt_service.database.models.enums.exam_enum import ExamStatus, AssignmentStatus
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.schemas.exam_schemas import (
    ExamCreate, ExamItemInternalRead, ExamSectionInternalRead, ExamUpdate, ExamSectionCreate,
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
    def _assert_can_edit(exam: ExamModel, current_user: CurrentUser):
        """Only the creating teacher (or admin/super_admin) can edit a DRAFT exam."""
        if exam.status not in (ExamStatus.DRAFT, ExamStatus.REJECTED):
            raise HTTPException(
                status_code=400,
                detail=f"Exam cannot be edited in '{exam.status}' status."
            )
        if current_user.role == UserRole.TEACHER and exam.created_by != current_user.id:
            raise HTTPException(status_code=403, detail="You can only edit your own exams.")

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

    @staticmethod
    def get_all(session: Session, current_user: CurrentUser) -> list[ExamModel]:
        query = select(ExamModel).where(ExamModel.org_id == current_user.org_id)

        # Teachers only see their own exams
        if current_user.role == UserRole.TEACHER:
            query = query.where(ExamModel.created_by == current_user.id)

        return session.exec(query).all()

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

        return exam

    @staticmethod
    def update(
        session: Session, exam_id: UUID,
        payload: ExamUpdate, current_user: CurrentUser
    ) -> ExamModel:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(exam, current_user)

        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(exam, key, value)

        exam.updated_at = datetime.now(timezone.utc)
        session.add(exam)
        _log(session, exam.id, exam.org_id, current_user.id, "updated")
        session.commit()
        session.refresh(exam)
        return exam

    @staticmethod
    def delete(session: Session, exam_id: UUID, current_user: CurrentUser):
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(exam, current_user)

        session.delete(exam)
        session.commit()

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
        item_count = len(session.exec(
            select(ExamItem).where(ExamItem.exam_id == exam_id)
        ).all())
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
        ExamService._assert_can_edit(exam, current_user)

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
        ExamService._get_exam(session, exam_id, current_user.org_id)
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
        ExamService._assert_can_edit(exam, current_user)

        # Get current max order
        existing = session.exec(
            select(ExamItem).where(ExamItem.exam_id == exam_id)
        ).all()
        next_order = len(existing)
        total_marks = sum(i.marks for i in existing)    # existing marks

        added = []
        for item_id in payload.item_ids:
            # Prevent duplicate items in same exam
            duplicate = session.exec(
                select(ExamItem).where(
                    ExamItem.exam_id == exam_id,
                    ExamItem.item_id == item_id,
                )
            ).first()
            if duplicate:
                continue

            exam_item = ExamItem(
                exam_id=exam_id,
                section_id=payload.section_id,
                item_id=item_id,
                org_id=current_user.org_id,
                order=next_order,
            )
            session.add(exam_item)
            added.append(exam_item)
            next_order += 1
            total_marks += exam_item.marks

        # Update total marks on exam
        exam.total_marks = total_marks  # + sum(i.marks for i in added)
        session.add(exam)
        session.commit()
        for item in added:
            session.refresh(item)
        return added

    @staticmethod
    def remove_item(
        session: Session, exam_id: UUID,
        exam_item_id: UUID, current_user: CurrentUser
    ):
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)
        ExamService._assert_can_edit(exam, current_user)

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
        ExamService._get_exam(session, exam_id, current_user.org_id)
        return session.exec(
            select(ExamItem).where(ExamItem.exam_id == exam_id).order_by(ExamItem.order)
        ).all()

    # --------------------------------------------------------
    # STUDENT ASSIGNMENT
    # --------------------------------------------------------

    @staticmethod
    def assign_students(
        session: Session, exam_id: UUID,
        payload: AssignStudentsRequest, current_user: CurrentUser
    ) -> list[ExamAssignment]:
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)

        if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
            raise HTTPException(
                status_code=400,
                detail="Students can only be assigned to approved or active exams."
            )

        assignments = []
        for student_id in payload.student_ids:
            existing = session.exec(
                select(ExamAssignment).where(
                    ExamAssignment.exam_id == exam_id,
                    ExamAssignment.student_id == student_id,
                )
            ).first()
            if existing:
                assignments.append(existing)
                continue

            assignment = ExamAssignment(
                exam_id=exam_id,
                student_id=student_id,
                org_id=current_user.org_id,
                assigned_by=current_user.id,
                scheduled_at=payload.scheduled_at,
            )
            session.add(assignment)
            assignments.append(assignment)

        session.commit()
        _log(session, exam_id, exam.org_id, current_user.id, "students_assigned",
             {"count": len(payload.student_ids)})
        session.commit()
        return assignments

    @staticmethod
    def get_assignments(
        session: Session, exam_id: UUID, current_user: CurrentUser
    ) -> list[ExamAssignment]:
        ExamService._get_exam(session, exam_id, current_user.org_id)
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
        session: Session,
        exam_id: UUID,
        cohort_id: UUID,
        scheduled_at: datetime | None,
        current_user: CurrentUser,
        student_ids: list[UUID],    # fetched from auth service
    ) -> list[ExamAssignment]:
        """
        Assign all students in a cohort to an exam.
        student_ids are fetched from auth service before calling this.
        """
        exam = ExamService._get_exam(session, exam_id, current_user.org_id)

        if exam.status not in (ExamStatus.APPROVED, ExamStatus.ACTIVE):
            raise HTTPException(
                status_code=400,
                detail="Students can only be assigned to approved or active exams."
            )

        assignments = []
        for student_id in student_ids:
            existing = session.exec(
                select(ExamAssignment).where(
                    ExamAssignment.exam_id == exam_id,
                    ExamAssignment.student_id == student_id,
                )
            ).first()
            if existing:
                assignments.append(existing)
                continue

            assignment = ExamAssignment(
                exam_id=exam_id,
                student_id=student_id,
                cohort_id=cohort_id,            # track which cohort this came from
                org_id=current_user.org_id,
                assigned_by=UUID(str(current_user.id)),
                scheduled_at=scheduled_at,
            )
            session.add(assignment)
            assignments.append(assignment)

        session.commit()
        try:
            _log(session, exam_id, current_user.org_id, UUID(str(current_user.id)),
                "assigned_to_cohort", {"cohort_id": str(cohort_id), "count": len(student_ids)})
            session.commit()
         
        except Exception:
            session.rollback()
            logger.exception(
                "Failed to write audit log for cohort_assigned on exam_id=%s cohort_id=%s",
                exam_id, cohort_id,
            )

        return assignments





    @staticmethod
    def get_sections_with_items_internal(session: Session, exam_id: UUID) -> list[ExamSectionInternalRead]:
        """
        Internal only — no org/role scoping needed here since the caller
        (attempt_service) already verified the student's attempt ownership.
        """
        sections = session.exec(
            select(ExamSection)
            .where(ExamSection.exam_id == exam_id)
            .order_by(ExamSection.order)
        ).all()

        result = []
        for section in sections:
            exam_items = session.exec(
                select(ExamItem)
                .where(ExamItem.section_id == section.id)
                .order_by(ExamItem.order)
            ).all()
            result.append(ExamSectionInternalRead(
                id=section.id,
                title=section.title,
                order=section.order,
                items=[
                    ExamItemInternalRead(item_id=ei.item_id, order=ei.order, marks=ei.marks)
                    for ei in exam_items
                ],
            ))
        return result



    @staticmethod
    def get_my_assignments(session: Session, current_user: CurrentUser) -> list[MyAssignmentRead]:
        assignments = session.exec(
            select(ExamAssignment, ExamModel)
            .join(ExamModel, ExamAssignment.exam_id == ExamModel.id)
            .where(
                ExamAssignment.student_id == current_user.id,
                ExamAssignment.org_id == current_user.org_id,
            )
            .order_by(ExamModel.start_time.desc().nulls_last())
        ).all()

        return [
            MyAssignmentRead(
                assignment_id=assignment.id,
                exam_id=exam.id,
                exam_title=exam.title,
                status=assignment.status,
                scheduled_at=assignment.scheduled_at,
                duration_minutes=exam.duration_minutes,
                start_time=exam.start_time,
                end_time=exam.end_time,
                has_attempted=assignment.status == AssignmentStatus.ASSIGNED,  # adjust to your actual status values
            )
            for assignment, exam in assignments
        ]
