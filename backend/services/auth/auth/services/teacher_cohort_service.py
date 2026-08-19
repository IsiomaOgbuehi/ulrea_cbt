from uuid import UUID
from fastapi import HTTPException
from sqlmodel import Session, select

from auth.database.schema.cohort.cohort_db import CohortModel, TeacherCohortAssignment
from auth.database.schema.user.user_db import UserModel


class TeacherCohortService:

    @staticmethod
    def assign(session: Session, cohort_id: UUID, teacher_id: UUID, org_id: UUID, actor_id: UUID) -> TeacherCohortAssignment:
        # Validate cohort belongs to org
        cohort = session.exec(
            select(CohortModel).where(CohortModel.id == cohort_id, CohortModel.org_id == org_id)
        ).first()
        if not cohort:
            raise HTTPException(status_code=404, detail="Cohort not found.")

        # Validate teacher exists and belongs to org
        teacher = session.exec(
            select(UserModel).where(UserModel.id == teacher_id)
        ).first()
        if not teacher:
            raise HTTPException(status_code=404, detail="Teacher not found.")

        # Prevent duplicate assignment
        existing = session.exec(
            select(TeacherCohortAssignment).where(
                TeacherCohortAssignment.cohort_id == cohort_id,
                TeacherCohortAssignment.teacher_id == teacher_id,
            )
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Teacher is already assigned to this cohort.")

        assignment = TeacherCohortAssignment(
            teacher_id=teacher_id,
            cohort_id=cohort_id,
            org_id=org_id,
            assigned_by=actor_id,
        )
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return assignment

    @staticmethod
    def unassign(session: Session, cohort_id: UUID, teacher_id: UUID, org_id: UUID) -> None:
        assignment = session.exec(
            select(TeacherCohortAssignment).where(
                TeacherCohortAssignment.cohort_id == cohort_id,
                TeacherCohortAssignment.teacher_id == teacher_id,
                TeacherCohortAssignment.org_id == org_id,
            )
        ).first()
        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found.")
        session.delete(assignment)
        session.commit()

    @staticmethod
    def list_teachers_for_cohort(
        session: Session, cohort_id: UUID, org_id: UUID
    ) -> list[tuple[TeacherCohortAssignment, UserModel]]:
        return session.exec(
            select(TeacherCohortAssignment, UserModel)
            .join(UserModel, UserModel.id == TeacherCohortAssignment.teacher_id)
            .where(
                TeacherCohortAssignment.cohort_id == cohort_id,
                TeacherCohortAssignment.org_id == org_id,
            )
        ).all()

    @staticmethod
    def list_cohorts_for_teacher(session: Session, teacher_id: UUID, org_id: UUID) -> list[CohortModel]:
        assignments = session.exec(
            select(TeacherCohortAssignment).where(
                TeacherCohortAssignment.teacher_id == teacher_id,
                TeacherCohortAssignment.org_id == org_id,
            )
        ).all()
        cohort_ids = [a.cohort_id for a in assignments]
        if not cohort_ids:
            return []
        return session.exec(
            select(CohortModel).where(CohortModel.id.in_(cohort_ids))
        ).all()

    @staticmethod
    def is_teacher_assigned(
        session: Session,
        cohort_id: UUID,
        teacher_id: UUID,
        org_id: UUID,
    ) -> bool:
        assignment = session.exec(
            select(TeacherCohortAssignment).where(
                TeacherCohortAssignment.cohort_id == cohort_id,
                TeacherCohortAssignment.teacher_id == teacher_id,
                TeacherCohortAssignment.org_id == org_id,
            )
        ).first()

        return assignment is not None