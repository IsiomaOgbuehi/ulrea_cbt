"""
Organisation difficulty levels (see models/difficulty.py).

DifficultyScope is what items / exams / bulk upload use to turn what was sent
("medium", "Medium", None) into a level key, so every question carries a level
the organisation actually has.
"""
import re
from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from cbt_service.database.models.difficulty import (
    BUILT_IN_LEVELS, DEFAULT_LEVEL_KEY, DifficultyLevel,
)
from cbt_service.database.models.enums.item_subject_enums import DifficultyStatus
from cbt_service.database.models.item import ItemModel
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.difficulty_schemas import (
    DifficultyLevelCreate, DifficultyLevelRead, DifficultyLevelUpdate, DifficultyReorder,
)

ACTIVE: str = DifficultyStatus.ACTIVE.value
ARCHIVED: str = DifficultyStatus.ARCHIVED.value


def _now() -> datetime:
    return datetime.now(timezone.utc)


# raw value from a spreadsheet cell -> (level key, error)
DifficultyResolver = Callable[[str | None], tuple[str | None, str | None]]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "level"


# ====================================================================
# Helpers for items / exams / bulk upload
# ====================================================================

class DifficultyScope:

    @staticmethod
    def levels(session: Session, org_id: UUID) -> list[DifficultyLevel]:
        """The organisation's levels in order; the built-in ones are created on first use."""
        rows: list[DifficultyLevel] = list(session.exec(
            select(DifficultyLevel)
            .where(DifficultyLevel.org_id == org_id)
            .order_by(col(DifficultyLevel.position), col(DifficultyLevel.created_at))
        ).all())
        if rows:
            return rows
        try:
            for position, (key, name, colour) in enumerate(BUILT_IN_LEVELS):
                session.add(DifficultyLevel(
                    org_id=org_id, key=key, name=name, colour=colour, position=position,
                    is_default=key == DEFAULT_LEVEL_KEY, is_built_in=True,
                ))
            session.commit()
        except IntegrityError:
            session.rollback()          # another request seeded them first
        return list(session.exec(
            select(DifficultyLevel)
            .where(DifficultyLevel.org_id == org_id)
            .order_by(col(DifficultyLevel.position), col(DifficultyLevel.created_at))
        ).all())

    @staticmethod
    def _match(levels: list[DifficultyLevel], raw: str) -> DifficultyLevel | None:
        """By key, else by name (both case-insensitive): "cat1", "CAT 1" and "Cat 1" all work."""
        wanted: str = raw.strip().casefold()
        for level in levels:
            if level.key == wanted:
                return level
        squashed: str = " ".join(wanted.split())
        for level in levels:
            if " ".join(level.name.casefold().split()) == squashed:
                return level
        return None

    @staticmethod
    def resolve(session: Session, org_id: UUID, raw: str | None, current: str | None = None) -> str:
        """
        A level key for a question. None -> the organisation's default.
        Archived levels can't be picked, except the one the question already has.
        """
        levels: list[DifficultyLevel] = DifficultyScope.levels(session, org_id)
        if raw is None or not raw.strip():
            default = next((l for l in levels if l.is_default and l.status == ACTIVE), None)
            default = default or next((l for l in levels if l.status == ACTIVE), None)
            if default is None:
                raise HTTPException(status_code=422, detail="Set up at least one difficulty level first.")
            return default.key
        level = DifficultyScope._match(levels, raw)
        if level is None:
            names: str = ", ".join(l.name for l in levels if l.status == ACTIVE)
            raise HTTPException(status_code=422, detail=f"Unknown difficulty '{raw}'. Use one of: {names}.")
        if level.status != ACTIVE and level.key != current:
            raise HTTPException(status_code=422, detail=f"The difficulty '{level.name}' is archived.")
        return level.key

    @staticmethod
    def filter_key(session: Session, org_id: UUID, raw: str | None) -> str | None:
        """For filters: a key or name of any level (archived too); unknown values match nothing."""
        if raw is None or not raw.strip():
            return None
        level = DifficultyScope._match(DifficultyScope.levels(session, org_id), raw)
        return level.key if level is not None else raw.strip().casefold()

    @staticmethod
    def bulk_resolver(session: Session, org_id: UUID) -> DifficultyResolver:
        """resolve() for many rows without re-reading the levels: returns (key, error)."""
        levels: list[DifficultyLevel] = DifficultyScope.levels(session, org_id)
        default: DifficultyLevel | None = next((l for l in levels if l.is_default and l.status == ACTIVE), None) \
            or next((l for l in levels if l.status == ACTIVE), None)
        names: str = ", ".join(l.name for l in levels if l.status == ACTIVE)

        def resolve(raw: str | None) -> tuple[str | None, str | None]:
            if raw is None or not raw.strip():
                if default is None:
                    return None, "no difficulty levels are set up"
                return default.key, None
            level = DifficultyScope._match(levels, raw)
            if level is None:
                return None, f"unknown difficulty '{raw}' (use one of: {names})"
            if level.status != ACTIVE:
                return None, f"difficulty '{level.name}' is archived"
            return level.key, None

        return resolve


# ====================================================================
# Managing levels (Settings › Question difficulty)
# ====================================================================

class DifficultyService:

    @staticmethod
    def _counts(session: Session, org_id: UUID) -> dict[str, int]:
        rows = session.exec(
            select(ItemModel.difficulty, func.count())
            .where(ItemModel.org_id == org_id)
            .group_by(ItemModel.difficulty)
        ).all()
        return {str(k): int(n) for k, n in rows}

    @staticmethod
    def _read(level: DifficultyLevel, counts: dict[str, int]) -> DifficultyLevelRead:
        return DifficultyLevelRead(
            id=level.id, key=level.key, name=level.name, description=level.description,
            colour=level.colour, position=level.position, is_default=level.is_default,
            is_built_in=level.is_built_in, status=DifficultyStatus(level.status),
            item_count=counts.get(level.key, 0),
            created_at=level.created_at, updated_at=level.updated_at,
        )

    @staticmethod
    def list_all(session: Session, current_user: CurrentUser, include_archived: bool = True) -> list[DifficultyLevelRead]:
        levels = DifficultyScope.levels(session, current_user.org_id)
        counts = DifficultyService._counts(session, current_user.org_id)
        return [
            DifficultyService._read(l, counts)
            for l in levels if include_archived or l.status == ACTIVE
        ]

    @staticmethod
    def _get(session: Session, level_id: UUID, org_id: UUID) -> DifficultyLevel:
        level = session.get(DifficultyLevel, level_id)
        if level is None or level.org_id != org_id:
            raise HTTPException(status_code=404, detail="Difficulty level not found.")
        return level

    @staticmethod
    def _assert_name_free(levels: list[DifficultyLevel], name: str, except_id: UUID | None = None) -> None:
        folded: str = name.casefold()
        if any(l.name.casefold() == folded and l.id != except_id for l in levels):
            raise HTTPException(status_code=409, detail=f"There's already a level called '{name}'.")

    @staticmethod
    def _make_default(session: Session, levels: list[DifficultyLevel], level: DifficultyLevel) -> None:
        for other in levels:
            if other.is_default and other.id != level.id:
                other.is_default = False
                other.updated_at = _now()
                session.add(other)
        level.is_default = True

    @staticmethod
    def create(session: Session, payload: DifficultyLevelCreate, current_user: CurrentUser) -> DifficultyLevelRead:
        org_id: UUID = current_user.org_id
        levels = DifficultyScope.levels(session, org_id)
        DifficultyService._assert_name_free(levels, payload.name)

        base: str = _slug(payload.name)
        taken: set[str] = {l.key for l in levels}
        key, n = base, 2
        while key in taken:
            suffix = f"-{n}"
            key, n = f"{base[:40 - len(suffix)]}{suffix}", n + 1

        level = DifficultyLevel(
            org_id=org_id, key=key, name=payload.name, description=payload.description,
            colour=payload.colour, position=max((l.position for l in levels), default=-1) + 1,
            created_by=current_user.id,
        )
        if payload.is_default:
            DifficultyService._make_default(session, levels, level)
        session.add(level)
        session.commit()
        session.refresh(level)
        return DifficultyService._read(level, {})

    @staticmethod
    def update(
        session: Session, level_id: UUID, payload: DifficultyLevelUpdate, current_user: CurrentUser,
    ) -> DifficultyLevelRead:
        org_id: UUID = current_user.org_id
        levels = DifficultyScope.levels(session, org_id)
        level = DifficultyService._get(session, level_id, org_id)
        data = payload.model_dump(exclude_unset=True)

        if data.get("name") is not None:
            DifficultyService._assert_name_free(levels, data["name"], level.id)
            level.name = data["name"]
        if "description" in data:
            level.description = (data["description"] or "").strip() or None
        if data.get("colour") is not None:
            level.colour = data["colour"]

        new_status = data.get("status")
        if new_status is not None:
            new_status = DifficultyStatus(new_status).value
            if new_status == ARCHIVED and level.is_default:
                raise HTTPException(
                    status_code=409, detail="This is the default level. Make another level the default first.",
                )
            if new_status == ARCHIVED and not any(l.status == ACTIVE and l.id != level.id for l in levels):
                raise HTTPException(status_code=409, detail="Keep at least one level active.")
            level.status = new_status

        if data.get("is_default") is True:
            if level.status != ACTIVE:
                raise HTTPException(status_code=409, detail="Restore this level before making it the default.")
            DifficultyService._make_default(session, levels, level)

        level.updated_at = _now()
        session.add(level)
        session.commit()
        session.refresh(level)
        return DifficultyService._read(level, DifficultyService._counts(session, org_id))

    @staticmethod
    def reorder(session: Session, payload: DifficultyReorder, current_user: CurrentUser) -> list[DifficultyLevelRead]:
        levels = DifficultyScope.levels(session, current_user.org_id)
        by_id: dict[UUID, DifficultyLevel] = {l.id: l for l in levels}
        if set(payload.ids) != set(by_id) or len(payload.ids) != len(by_id):
            raise HTTPException(status_code=422, detail="Send every level's id exactly once.")
        for position, level_id in enumerate(payload.ids):
            level = by_id[level_id]
            if level.position != position:
                level.position = position
                level.updated_at = _now()
                session.add(level)
        session.commit()
        return DifficultyService.list_all(session, current_user)

    @staticmethod
    def delete(session: Session, level_id: UUID, current_user: CurrentUser) -> None:
        org_id: UUID = current_user.org_id
        level = DifficultyService._get(session, level_id, org_id)
        if level.is_built_in:
            raise HTTPException(status_code=409, detail="Built-in levels can be archived, not deleted.")
        if level.is_default:
            raise HTTPException(status_code=409, detail="Make another level the default first.")
        used: int = DifficultyService._counts(session, org_id).get(level.key, 0)
        if used:
            raise HTTPException(
                status_code=409,
                detail=f"{used} question{'s' if used != 1 else ''} use{'' if used != 1 else 's'} this level. Archive it instead.",
            )
        session.delete(level)
        session.commit()
