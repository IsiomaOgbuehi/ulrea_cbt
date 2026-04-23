from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from uuid import UUID
from sqlmodel import select
from auth.database.schema.user.user_db import UserModel
from auth.database.database import SessionDep
from auth.core.settings import settings

router = APIRouter(prefix="/internal", tags=["internal"])

def verify_internal_secret(x_internal_secret: str = Header(...)):
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden.")
    
class UserSummaryResponse(BaseModel):
    id: UUID
    firstname: str
    lastname: str
    email: str | None
    role: str


class BulkUserRequest(BaseModel):
    user_ids: list[UUID]


@router.get("/users/{user_id}", response_model=UserSummaryResponse)
async def get_user_internal(
    user_id: UUID,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    user = session.exec(
        select(UserModel).where(UserModel.id == user_id)
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserSummaryResponse.model_validate(user, from_attributes=True)


@router.get("/users/bulk", response_model=list[UserSummaryResponse])
async def get_users_bulk_internal(
    payload: BulkUserRequest,
    session: SessionDep,
    _: str = Depends(verify_internal_secret),
):
    users = session.exec(
        select(UserModel).where(UserModel.id.in_(payload.user_ids))
    ).all()
    return [UserSummaryResponse.model_validate(u, from_attributes=True) for u in users]