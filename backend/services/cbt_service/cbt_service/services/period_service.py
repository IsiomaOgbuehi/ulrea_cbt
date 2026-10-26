"""
Organisation-wide periods (session > term, year > cycle...) and org settings.

PeriodScope holds the small helpers items, exams and bulk upload use; it only
imports models so item_service / exam_service can use it without a cycle.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

from cbt_service.database.models.enums.item_subject_enums import ItemStatus, PeriodStatus
from cbt_service.database.models.exam import ExamModel
from cbt_service.database.models.item import ItemModel
from cbt_service.database.models.period import (
    DEFAULT_PERIOD_CHILD_LABEL, DEFAULT_PERIOD_PARENT_LABEL, OrgSettings, Period,
)
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.schemas.period_schemas import (
    OrgSettingsRead, OrgSettingsUpdate, PeriodCreate, PeriodRead, PeriodUpdate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ====================================================================
# Helpers for items / exams
# ====================================================================

class PeriodScope:

    @staticmethod
    def get(session: Session, period_id: UUID, org_id: UUID) -> Period:
        period: Period | None = session.exec(
            select(Period).where(Period.id == period_id, Period.org_id == org_id)
        ).first()
        if period is None:
            raise HTTPException(status_code=400, detail="Period not found.")
        return period

    @staticmethod
    def validate(session: Session, period_id: UUID | None, org_id: UUID) -> None:
        """A period used for new content must exist and not be archived (nor its parent)."""
        if period_id is None:
            return
        period = PeriodScope.get(session, period_id, org_id)
        archived: str = PeriodStatus.ARCHIVED.value
        if period.status == archived:
            raise HTTPException(status_code=400, detail=f"Period '{period.name}' is archived.")
        if period.parent_id is not None:
            parent = PeriodScope.get(session, period.parent_id, org_id)
            if parent.status == archived:
                raise HTTPException(status_code=400, detail=f"Period '{parent.name}' is archived.")

    @staticmethod
    def with_children(session: Session, period_id: UUID, org_id: UUID) -> list[UUID]:
        """The period plus its children, so filtering by a session includes its terms."""
        PeriodScope.get(session, period_id, org_id)
        children: list[UUID] = list(
            session.exec(
                select(Period.id).where(Period.parent_id == period_id, Period.org_id == org_id)
            ).all()
        )
        return [period_id, *children]

    @staticmethod
    def lookup(session: Session, org_id: UUID) -> dict[str, UUID]:
        """
        Case-insensitive names -> period id, for spreadsheet text. Each period
        is reachable as "Parent > Child", and by its bare name when unique.
        """
        periods: list[Period] = list(session.exec(select(Period).where(Period.org_id == org_id)).all())
        by_id: dict[UUID, Period] = {p.id: p for p in periods}
        result: dict[str, UUID] = {}
        ambiguous: set[str] = set()
        for period in periods:
            key = period.name.casefold()
            if key in result and result[key] != period.id:
                ambiguous.add(key)
            result[key] = period.id
            parent = by_id.get(period.parent_id) if period.parent_id else None
            if parent is not None:
                result[f"{parent.name.casefold()} > {key}"] = period.id
        for key in ambiguous:
            result.pop(key, None)
        return result


# ====================================================================
# Periods
# ====================================================================

class PeriodService:

    @staticmethod
    def _counts(session: Session, org_id: UUID) -> tuple[dict[UUID, int], dict[UUID, int]]:
        item_rows = session.exec(
            select(ItemModel.period_id, func.count())
            .where(ItemModel.org_id == org_id, ItemModel.status == ItemStatus.ACTIVE)
            .where(col(ItemModel.period_id).is_not(None))
            .group_by(col(ItemModel.period_id))
        ).all()
        exam_rows = session.exec(
            select(ExamModel.period_id, func.count())
            .where(ExamModel.org_id == org_id)
            .where(col(ExamModel.period_id).is_not(None))
            .group_by(col(ExamModel.period_id))
        ).all()
        items: dict[UUID, int] = {pid: int(n) for pid, n in item_rows if pid is not None}
        exams: dict[UUID, int] = {pid: int(n) for pid, n in exam_rows if pid is not None}
        return items, exams

    @staticmethod
    def _current_id(session: Session, org_id: UUID) -> UUID | None:
        settings: OrgSettings | None = session.get(OrgSettings, org_id)
        return settings.current_period_id if settings else None

    @staticmethod
    def _to_read(
        period: Period, items: dict[UUID, int], exams: dict[UUID, int], current_id: UUID | None
    ) -> PeriodRead:
        return PeriodRead.model_validate({
            **period.model_dump(),
            "item_count": items.get(period.id, 0),
            "exam_count": exams.get(period.id, 0),
            "is_current": period.id == current_id,
        })

    @staticmethod
    def _read_one(session: Session, period: Period) -> PeriodRead:
        items, exams = PeriodService._counts(session, period.org_id)
        return PeriodService._to_read(period, items, exams, PeriodService._current_id(session, period.org_id))

    @staticmethod
    def _assert_unique_sibling(
        session: Session, org_id: UUID, parent_id: UUID | None, name: str, exclude_id: UUID | None = None
    ) -> None:
        query = select(Period.id).where(
            Period.org_id == org_id,
            func.lower(Period.name) == name.casefold(),
            (col(Period.parent_id).is_(None) if parent_id is None else Period.parent_id == parent_id),
        )
        if exclude_id is not None:
            query = query.where(Period.id != exclude_id)
        if session.exec(query).first() is not None:
            raise HTTPException(status_code=409, detail=f"'{name}' already exists here.")

    @staticmethod
    def list(session: Session, current_user: CurrentUser, include_archived: bool = False) -> list[PeriodRead]:
        """Flat list ordered for display; nest using parent_id."""
        query = select(Period).where(Period.org_id == current_user.org_id)
        if not include_archived:
            query = query.where(Period.status == PeriodStatus.ACTIVE.value)
        periods: list[Period] = list(
            session.exec(query.order_by(col(Period.position), col(Period.starts_on), col(Period.name))).all()
        )
        if not include_archived:
            # Terms of an archived session are hidden with it.
            shown: set[UUID] = {p.id for p in periods}
            periods = [p for p in periods if p.parent_id is None or p.parent_id in shown]
        items, exams = PeriodService._counts(session, current_user.org_id)
        current_id = PeriodService._current_id(session, current_user.org_id)
        return [PeriodService._to_read(p, items, exams, current_id) for p in periods]

    @staticmethod
    def create(session: Session, payload: PeriodCreate, current_user: CurrentUser) -> PeriodRead:
        if payload.parent_id is not None:
            parent = PeriodScope.get(session, payload.parent_id, current_user.org_id)
            if parent.parent_id is not None:
                raise HTTPException(
                    status_code=400, detail="Periods have two levels: add this under a top-level period."
                )
            if parent.status == PeriodStatus.ARCHIVED.value:
                raise HTTPException(status_code=400, detail=f"'{parent.name}' is archived.")
        PeriodService._assert_unique_sibling(session, current_user.org_id, payload.parent_id, payload.name)
        period = Period(
            org_id=current_user.org_id,
            parent_id=payload.parent_id,
            name=payload.name,
            starts_on=payload.starts_on,
            ends_on=payload.ends_on,
            position=payload.position,
            created_by=current_user.id,
        )
        session.add(period)
        session.commit()
        session.refresh(period)
        return PeriodService._read_one(session, period)

    @staticmethod
    def update(
        session: Session, period_id: UUID, payload: PeriodUpdate, current_user: CurrentUser
    ) -> PeriodRead:
        period = PeriodScope.get(session, period_id, current_user.org_id)
        data = payload.model_dump(exclude_unset=True)

        if data.get("name"):
            PeriodService._assert_unique_sibling(
                session, current_user.org_id, period.parent_id, data["name"], exclude_id=period.id
            )
        starts_on = data.get("starts_on", period.starts_on)
        ends_on = data.get("ends_on", period.ends_on)
        if starts_on and ends_on and ends_on < starts_on:
            raise HTTPException(status_code=400, detail="The end date must be on or after the start date.")

        status = data.pop("status", None)
        if status is not None:
            period.status = status.value
            if status == PeriodStatus.ARCHIVED:
                PeriodService._clear_current_if_within(session, period)
            elif period.parent_id is not None:
                # Restoring a term inside an archived session restores the session too.
                parent = PeriodScope.get(session, period.parent_id, current_user.org_id)
                if parent.status == PeriodStatus.ARCHIVED.value:
                    parent.status = PeriodStatus.ACTIVE.value
                    parent.updated_at = _now()
                    session.add(parent)

        for key, value in data.items():
            if key == "name" and not value:
                continue
            setattr(period, key, value)
        period.updated_at = _now()
        session.add(period)
        session.commit()
        session.refresh(period)
        return PeriodService._read_one(session, period)

    @staticmethod
    def delete(session: Session, period_id: UUID, current_user: CurrentUser) -> None:
        """Only for periods nothing uses yet (e.g. created by mistake); archive the rest."""
        period = PeriodScope.get(session, period_id, current_user.org_id)
        ids: list[UUID] = PeriodScope.with_children(session, period.id, current_user.org_id)
        in_use = session.exec(
            select(ItemModel.id).where(col(ItemModel.period_id).in_(ids)).limit(1)
        ).first() or session.exec(
            select(ExamModel.id).where(col(ExamModel.period_id).in_(ids)).limit(1)
        ).first()
        if in_use is not None:
            raise HTTPException(
                status_code=409,
                detail="Questions or exams use this period. Archive it instead to hide it from lists.",
            )
        PeriodService._clear_current_if_within(session, period)
        session.flush()
        for child_id in ids[1:]:
            child = session.get(Period, child_id)
            if child is not None:
                session.delete(child)
        session.flush()
        session.delete(period)
        session.commit()

    @staticmethod
    def _clear_current_if_within(session: Session, period: Period) -> None:
        settings: OrgSettings | None = session.get(OrgSettings, period.org_id)
        if settings is None or settings.current_period_id is None:
            return
        current: Period | None = session.get(Period, settings.current_period_id)
        if current is not None and (current.id == period.id or current.parent_id == period.id):
            settings.current_period_id = None
            settings.updated_at = _now()
            session.add(settings)


# ====================================================================
# Organisation settings
# ====================================================================

class OrgSettingsService:

    @staticmethod
    def _to_read(settings: OrgSettings | None) -> OrgSettingsRead:
        if settings is None:
            return OrgSettingsRead(
                period_parent_label=DEFAULT_PERIOD_PARENT_LABEL,
                period_child_label=DEFAULT_PERIOD_CHILD_LABEL,
                current_period_id=None,
                is_default=True,
                updated_at=None,
            )
        return OrgSettingsRead(
            period_parent_label=settings.period_parent_label,
            period_child_label=settings.period_child_label,
            current_period_id=settings.current_period_id,
            is_default=False,
            updated_at=settings.updated_at,
        )

    @staticmethod
    def get(session: Session, current_user: CurrentUser) -> OrgSettingsRead:
        return OrgSettingsService._to_read(session.get(OrgSettings, current_user.org_id))

    @staticmethod
    def update(session: Session, payload: OrgSettingsUpdate, current_user: CurrentUser) -> OrgSettingsRead:
        data = payload.model_dump(exclude_unset=True)
        if data.get("current_period_id") is not None:
            PeriodScope.validate(session, data["current_period_id"], current_user.org_id)

        settings: OrgSettings | None = session.get(OrgSettings, current_user.org_id)
        if settings is None:
            settings = OrgSettings(org_id=current_user.org_id)
        for key, value in data.items():
            if key in ("period_parent_label", "period_child_label") and not value:
                continue
            setattr(settings, key, value)
        settings.updated_by = current_user.id
        settings.updated_at = _now()
        session.add(settings)
        session.commit()
        session.refresh(settings)
        return OrgSettingsService._to_read(settings)

