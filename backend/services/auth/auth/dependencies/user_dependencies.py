
from typing import Annotated

from fastapi import Depends, HTTPException, status

# from auth.api_models.user import User, UserInDB
from auth.database.schema import UserModel, UserRead
from auth.dependencies.auth_dependencies import get_current_user as current_user
from auth.utility.password.password_harsher import PasswordHasher
from sqlmodel import Session, select



def get_user(db: Session, email: str) -> UserModel:
    user = db.exec(
        select(UserModel).where(UserModel.email == email)
    ).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    return UserModel.model_validate(user)

def authenticate_user(db:Session, email: str, password: str) -> UserRead:
    user = get_user(db, email)
    if not user:
        return False
    if not PasswordHasher.verify(password, user.password):
        return False
    return UserRead.model_validate(user)

async def get_current_user(token: str):
    return await current_user(token=token)



async def get_current_active_user(
    current_user: Annotated[UserRead, Depends(get_current_user)],
):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user