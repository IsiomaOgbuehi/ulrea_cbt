from typing import Annotated
from uuid import UUID
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt

from item_bank_service.core.settings import settings
from item_bank_service.schemas.schemas import CurrentUser
from item_bank_service.database.models.enums import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> CurrentUser:
    """
    Verifies the JWT issued by the auth service.
    Item bank service trusts the token — no DB lookup needed here.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise credentials_exception

    if payload.get("type") != "access":
        raise credentials_exception

    try:
        user_id = UUID(payload.get("sub"))
        org_id = UUID(payload.get("org_id"))
    except (ValueError, AttributeError, TypeError):
        raise credentials_exception

    return CurrentUser(
        id=user_id,
        org_id=org_id,
        role=payload.get("role", ""),
        email=payload.get("email"),
        verified=payload.get("verified", False),
    )


def require_roles(*roles: UserRole):
    """Role-based access dependency."""
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in [r.value for r in roles]:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return current_user
    return _check


# Convenience dependencies
AdminOrAbove = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN))
TeacherOrAbove = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN, UserRole.TEACHER))
