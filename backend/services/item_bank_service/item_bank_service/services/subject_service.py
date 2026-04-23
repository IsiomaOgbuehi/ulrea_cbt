from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select

from item_bank_service.database.models.subject import SubjectModel, SubjectAssignment
from item_bank_service.schemas.schemas import SubjectCreate, SubjectUpdate, CurrentUser
from item_bank_service.database.models.enums import UserRole


class SubjectService:

    @staticmethod
    def create(session: Session, payload: SubjectCreate, current_user: CurrentUser) -> SubjectModel:
        subject = SubjectModel(
            org_id=current_user.org_id,
            created_by=current_user.id,
            name=payload.name,
            description=payload.description,
        )
        session.add(subject)
        session.commit()
        session.refresh(subject)
        return subject

    @staticmethod
    def get_all(session: Session, current_user: CurrentUser) -> list[SubjectModel]:
        """
        Admin/Super Admin see all subjects in their org.
        Teachers only see subjects assigned to them.
        """
        query = select(SubjectModel).where(
            SubjectModel.org_id == current_user.org_id,
            SubjectModel.status == "active",
        )

        if current_user.role == UserRole.TEACHER:
            # Join to assignments — teacher only sees assigned subjects
            assigned_subject_ids = select(SubjectAssignment.subject_id).where(
                SubjectAssignment.assigned_to == current_user.id,
                SubjectAssignment.org_id == current_user.org_id,
            )
            query = query.where(SubjectModel.id.in_(assigned_subject_ids))

        return session.exec(query).all()

    @staticmethod
    def get_by_id(session: Session, subject_id: UUID, current_user: CurrentUser) -> SubjectModel:
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,  # tenant guard
            )
        ).first()

        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        # Teachers must be assigned to view
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
    def update(
        session: Session,
        subject_id: UUID,
        payload: SubjectUpdate,
        current_user: CurrentUser,
    ) -> SubjectModel:
        subject = SubjectService.get_by_id(session, subject_id, current_user)

        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(subject, key, value)

        subject.updated_at = datetime.now(timezone.utc)
        session.add(subject)
        session.commit()
        session.refresh(subject)
        return subject

    @staticmethod
    def archive(session: Session, subject_id: UUID, current_user: CurrentUser) -> SubjectModel:
        subject = SubjectService.get_by_id(session, subject_id, current_user)
        subject.status = "archived"
        subject.updated_at = datetime.now(timezone.utc)
        session.add(subject)
        session.commit()
        session.refresh(subject)
        return subject

    @staticmethod
    def assign_users(
        session: Session,
        subject_id: UUID,
        user_ids: list[UUID],
        current_user: CurrentUser,
    ) -> list[SubjectAssignment]:
        # Verify subject exists and belongs to org
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,
            )
        ).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        assignments = []
        for user_id in user_ids:
            # Skip if already assigned
            existing = session.exec(
                select(SubjectAssignment).where(
                    SubjectAssignment.subject_id == subject_id,
                    SubjectAssignment.assigned_to == user_id,
                )
            ).first()
            if existing:
                assignments.append(existing)
                continue

            assignment = SubjectAssignment(
                subject_id=subject_id,
                org_id=current_user.org_id,
                assigned_to=user_id,
                assigned_by=current_user.id,
            )
            session.add(assignment)
            assignments.append(assignment)

        session.commit()
        return assignments

    @staticmethod
    def unassign_user(
        session: Session,
        subject_id: UUID,
        user_id: UUID,
        current_user: CurrentUser,
    ) -> None:
        assignment = session.exec(
            select(SubjectAssignment).where(
                SubjectAssignment.subject_id == subject_id,
                SubjectAssignment.assigned_to == user_id,
                SubjectAssignment.org_id == current_user.org_id,
            )
        ).first()

        if not assignment:
            raise HTTPException(status_code=404, detail="Assignment not found.")

        session.delete(assignment)
        session.commit()

    @staticmethod
    def get_assignments(
        session: Session,
        subject_id: UUID,
        current_user: CurrentUser,
    ) -> list[SubjectAssignment]:
        # Verify subject belongs to org
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,
            )
        ).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        return session.exec(
            select(SubjectAssignment).where(
                SubjectAssignment.subject_id == subject_id,
            )
        ).all()
