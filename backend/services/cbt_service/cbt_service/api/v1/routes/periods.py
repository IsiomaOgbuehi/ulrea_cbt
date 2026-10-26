from uuid import UUID

from fastapi import APIRouter, Depends, Query

from cbt_service.database.database import SessionDep
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.schemas.period_schemas import (
    OrgSettingsRead, OrgSettingsUpdate, PeriodCreate, PeriodRead, PeriodUpdate,
)
from cbt_service.services.period_service import OrgSettingsService, PeriodService
from cbt_service.core.permissions import P
from cbt_service.dependencies import require_candidate_or, require_permission, require_staff

router = APIRouter(tags=["periods & organisation settings"])

# Anyone who works with questions or exams needs to see periods to tag / filter.


# ---------------------------------------------------------------- periods

''' LIST PERIODS 🗓️ '''
@router.get("/periods", response_model=list[PeriodRead])
def list_periods(
    session: SessionDep,
    include_archived: bool = Query(default=False),
    current_user: CurrentUser = Depends(require_staff()),
) -> list[PeriodRead]:
    """The organisation's sessions/terms (or years/cycles) as a flat list; nest by parent_id."""
    return PeriodService.list(session, current_user, include_archived)


''' CREATE PERIOD ➕ '''
@router.post("/periods", response_model=PeriodRead, status_code=201)
def create_period(
    payload: PeriodCreate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> PeriodRead:
    """Top-level period (parent_id null) or a period inside one."""
    return PeriodService.create(session, payload, current_user)


''' UPDATE PERIOD ✏️ '''
@router.patch("/periods/{period_id}", response_model=PeriodRead)
def update_period(
    period_id: UUID,
    payload: PeriodUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> PeriodRead:
    """Rename, change dates, reorder, archive or restore (status)."""
    return PeriodService.update(session, period_id, payload, current_user)


''' DELETE PERIOD ❌ '''
@router.delete("/periods/{period_id}", status_code=204)
def delete_period(
    period_id: UUID,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> None:
    """Deletes an unused period (and its unused children). In-use periods must be archived."""
    PeriodService.delete(session, period_id, current_user)


# ------------------------------------------------------- org settings

''' GET ORGANISATION SETTINGS ⚙️ '''
@router.get("/org-settings", response_model=OrgSettingsRead)
def get_org_settings(
    session: SessionDep,
    current_user: CurrentUser = Depends(require_staff()),
) -> OrgSettingsRead:
    return OrgSettingsService.get(session, current_user)


''' UPDATE ORGANISATION SETTINGS ⚙️ '''
@router.patch("/org-settings", response_model=OrgSettingsRead)
def update_org_settings(
    payload: OrgSettingsUpdate,
    session: SessionDep,
    current_user: CurrentUser = Depends(require_permission(P.SETTINGS_MANAGE)),
) -> OrgSettingsRead:
    """Period labels and the current period (send current_period_id: null to clear)."""
    return OrgSettingsService.update(session, payload, current_user)
