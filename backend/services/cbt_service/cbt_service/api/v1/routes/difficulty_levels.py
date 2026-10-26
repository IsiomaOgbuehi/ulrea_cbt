from uuid import UUID

from fastapi import APIRouter, Depends, Query

from cbt_service.core.permissions import P
from cbt_service.database.database import SessionDep
from cbt_service.dependencies import require_permission, require_staff
from cbt_service.schemas.current_user import CurrentUser
from cbt_service.schemas.difficulty_schemas import (
    DifficultyLevelCreate, DifficultyLevelRead, DifficultyLevelUpdate, DifficultyReorder,
)
from cbt_service.services.difficulty_service import DifficultyService

router = APIRouter(prefix="/difficulty-levels", tags=["difficulty levels"])


''' LIST DIFFICULTY LEVELS 📶 '''
@router.get("", response_model=list[DifficultyLevelRead])
def list_levels(
    session: SessionDep,
    include_archived: bool = Query(default=True),
    current_user: CurrentUser = Depends(require_staff()),
) -> list[DifficultyLevelRead]:
    """The organisation's levels, easiest first, with how many questions use each."""
    return DifficultyService.list_all(session, current_user, include_archived)


''' CREATE DIFFICULTY LEVEL ➕ '''
@router.post("", response_model=DifficultyLevelRead, status_code=201)
def create_level(
    payload: DifficultyLevelCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> DifficultyLevelRead:
    return DifficultyService.create(session, payload, current_user)


''' REORDER DIFFICULTY LEVELS ↕️ '''
@router.put("/order", response_model=list[DifficultyLevelRead])
def reorder_levels(
    payload: DifficultyReorder,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> list[DifficultyLevelRead]:
    return DifficultyService.reorder(session, payload, current_user)


''' UPDATE DIFFICULTY LEVEL ✏️ '''
@router.patch("/{level_id}", response_model=DifficultyLevelRead)
def update_level(
    level_id: UUID,
    payload: DifficultyLevelUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> DifficultyLevelRead:
    """Rename, recolour, make default, archive or restore."""
    return DifficultyService.update(session, level_id, payload, current_user)


''' DELETE DIFFICULTY LEVEL ❌ '''
@router.delete("/{level_id}", status_code=204)
def delete_level(
    level_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> None:
    """Only unused custom levels; built-in or used ones are archived instead."""
    DifficultyService.delete(session, level_id, current_user)
