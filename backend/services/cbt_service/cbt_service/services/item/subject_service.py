from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, col, func, not_

from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.subject import SubjectModel, SubjectAssignment
from cbt_service.schemas.item_subject_schemas import SubjectCreate, SubjectUpdate, CurrentUser
from cbt_service.database.models.enums.item_subject_enums import ItemStatus, SubjectStatus
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.core.permissions import P


def _escape_like(term: str) -> str:
        """Treat %, _ and \\ in user input literally inside an ILIKE pattern."""
        return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

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

    # @staticmethod
    # def get_all(session: Session, current_user: CurrentUser) -> list[SubjectModel]:
    #     """
    #     Admin/Super Admin see all subjects in their org.
    #     Teachers only see subjects assigned to them.
    #     """
    #     query = select(SubjectModel).where(
    #         SubjectModel.org_id == current_user.org_id,
    #         SubjectModel.status == SubjectStatus.ACTIVE,
    #     )

    #     if current_user.role == UserRole.TEACHER:
    #         # Join to assignments — teacher only sees assigned subjects
    #         assigned_subject_ids = select(SubjectAssignment.subject_id).where(
    #             SubjectAssignment.assigned_to == current_user.id,
    #             SubjectAssignment.org_id == current_user.org_id,
    #         )
    #         query = query.where(SubjectModel.id.in_(assigned_subject_ids))

    #     return session.exec(query).all()

    @staticmethod
    def get_all(
        session: Session,
        current_user: CurrentUser,
        status: SubjectStatus | None = None,
        has_items: bool | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[tuple[SubjectModel, int]], int]:
        """
        Admin/Super Admin see all subjects in their org; teachers only their assigned ones.
        Returns ([(subject, active_item_count), ...] for the requested page, total matches).
        """
        query = select(SubjectModel).where(
            SubjectModel.org_id == current_user.org_id,
            SubjectModel.status == (status or SubjectStatus.ACTIVE),
        )

        if current_user.reach(P.SUBJECTS_VIEW) != "all":
            assigned_subject_ids = select(SubjectAssignment.subject_id).where(
                SubjectAssignment.assigned_to == current_user.id,
                SubjectAssignment.org_id == current_user.org_id,
            )
            query = query.where(col(SubjectModel.id).in_(assigned_subject_ids))

        if search and search.strip():
            pattern: str = f"%{_escape_like(search.strip())}%"
            query = query.where(col(SubjectModel.name).ilike(pattern, escape="\\"))

        if has_items is not None:
            has_active_items = (
                select(ItemModel.id)
                .where(
                    ItemModel.subject_id == SubjectModel.id,
                    ItemModel.org_id == current_user.org_id,
                    ItemModel.status == ItemStatus.ACTIVE,
                )
                .exists()
            )
            query = query.where(has_active_items if has_items else not_(has_active_items))

        # 1. Total matches, before limit/offset.
        total: int = session.exec(select(func.count()).select_from(query.subquery())).one()

        # 2. The requested page. The id tiebreaker keeps page boundaries stable
        #    when two subjects share a name.
        subjects: list[SubjectModel] = list(
            session.exec(
                query
                .order_by(col(SubjectModel.name), col(SubjectModel.id))
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
        )

        # 3. Active item counts for this page's subjects only.
        counts: dict[UUID, int] = {}
        if subjects:
            rows = session.exec(
                select(ItemModel.subject_id, func.count())
                .where(
                    ItemModel.org_id == current_user.org_id,
                    ItemModel.status == ItemStatus.ACTIVE,
                    col(ItemModel.subject_id).in_([s.id for s in subjects]),
                )
                .group_by(ItemModel.subject_id)
            ).all()
            counts = {subject_id: int(n) for subject_id, n in rows}

        return [(s, counts.get(s.id, 0)) for s in subjects], total

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

        # Without org-wide reach, only assigned subjects are visible
        if current_user.reach(P.SUBJECTS_VIEW) != "all":
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
        subject.status = SubjectStatus.ARCHIVED
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

        unique_ids: list[UUID] = list(dict.fromkeys(user_ids))
        existing: dict[UUID, SubjectAssignment] = {
            a.assigned_to: a
            for a in session.exec(
                select(SubjectAssignment).where(
                    SubjectAssignment.subject_id == subject_id,
                    col(SubjectAssignment.assigned_to).in_(unique_ids),
                )
            ).all()
        }
        created = {
            uid: SubjectAssignment(
                subject_id=subject_id, org_id=current_user.org_id,
                assigned_to=uid, assigned_by=current_user.id,
            )
            for uid in unique_ids if uid not in existing
        }
        session.add_all(created.values())
        session.commit()
        return [existing.get(uid) or created[uid] for uid in unique_ids]
        
        # Verify subject exists and belongs to org
        # subject = session.exec(
        #     select(SubjectModel).where(
        #         SubjectModel.id == subject_id,
        #         SubjectModel.org_id == current_user.org_id,
        #     )
        # ).first()
        # if not subject:
        #     raise HTTPException(status_code=404, detail="Subject not found.")

        # assignments = []
        # for user_id in user_ids:
        #     # Skip if already assigned
        #     existing = session.exec(
        #         select(SubjectAssignment).where(
        #             SubjectAssignment.subject_id == subject_id,
        #             SubjectAssignment.assigned_to == user_id,
        #         )
        #     ).first()
        #     if existing:
        #         assignments.append(existing)
        #         continue

        #     assignment = SubjectAssignment(
        #         subject_id=subject_id,
        #         org_id=current_user.org_id,
        #         assigned_to=user_id,
        #         assigned_by=current_user.id,
        #     )
        #     session.add(assignment)
        #     assignments.append(assignment)

        # session.commit()
        # return assignments

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

    
    @staticmethod
    def get_assignments_with_subject(
        session: Session, subject_id: UUID, current_user: CurrentUser
    ) -> tuple[SubjectModel, list[SubjectAssignment]]:
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == current_user.org_id,
            )
        ).first()
        if subject is None:
            raise HTTPException(status_code=404, detail="Subject not found.")
        assignments = list(
            session.exec(
                select(SubjectAssignment).where(SubjectAssignment.subject_id == subject_id)
            ).all()
        )
        return subject, assignments


    @staticmethod
    def get_assigned_user_ids(session: Session, subject_id: UUID, org_id: UUID) -> list[UUID]:
        """Used internally by auth_service to filter staff listings by subject assignment."""
        subject = session.exec(
            select(SubjectModel).where(
                SubjectModel.id == subject_id,
                SubjectModel.org_id == org_id,
            )
        ).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found.")

        return session.exec(
            select(SubjectAssignment.assigned_to).where(
                SubjectAssignment.subject_id == subject_id,
                SubjectAssignment.org_id == org_id,
            )
        ).all()


    @staticmethod
    def get_by_assigned_user(
        session: Session,
        user_id: UUID,
        current_user: CurrentUser,
        include_archived: bool = False,
    ) -> list[SubjectModel]:
        """
        Return all subjects assigned to `user_id` within the caller's org.
        Admins/Super Admins can query any user; everyone else can only query themselves.
        """
        if not current_user.can(P.STAFF_VIEW) and user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="You can only view your own subject assignments.",
            )

        query = (
            select(SubjectModel)
            .join(SubjectAssignment, SubjectAssignment.subject_id == SubjectModel.id)
            .where(
                SubjectAssignment.assigned_to == user_id,
                SubjectAssignment.org_id == current_user.org_id,  # tenant guard
                SubjectModel.org_id == current_user.org_id,       # tenant guard
            )
            .order_by(SubjectModel.name)
        )

        if not include_archived:
            query = query.where(SubjectModel.status == SubjectStatus.ACTIVE)

        return list(session.exec(query).all())

    
