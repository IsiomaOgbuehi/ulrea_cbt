from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException

from cbt_service.database.database import SessionDep
from cbt_service.services.item.subject_service import SubjectService
from cbt_service.core.settings import settings

router = APIRouter(prefix="/internal", tags=["internal"])


def verify_internal_secret(x_internal_secret: str = Header(...)):
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal service credentials.")


@router.get(
    "/subjects/{subject_id}/assigned-user-ids",
    response_model=list[UUID],
    dependencies=[Depends(verify_internal_secret)],
)
async def get_assigned_user_ids(
    subject_id: UUID,
    org_id: UUID,
    session: SessionDep,
):
    return SubjectService.get_assigned_user_ids(session=session, subject_id=subject_id, org_id=org_id)