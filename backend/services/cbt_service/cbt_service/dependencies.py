from collections.abc import Callable
from typing import Annotated
from uuid import UUID
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer
import jwt

from cbt_service.core.settings import settings
from cbt_service.schemas.item_subject_schemas import CurrentUser
from cbt_service.database.models.enums.enums import UserRole
from cbt_service.core.permissions import P

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
        perms=payload.get("perms"),
    )


def require_permission(*perms: P) -> Callable[..., CurrentUser]:
    """
    Allow staff holding ANY of these permissions (at any reach). Which records
    they may touch is then checked in the service using current_user.reach().
    """
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.is_candidate or not any(current_user.can(p) for p in perms):
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return current_user
    return _check


def require_staff() -> Callable[..., CurrentUser]:
    """Any signed-in staff member, whatever their permissions (e.g. reading periods)."""
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.is_candidate:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return current_user
    return _check


def require_candidate_or(*perms: P) -> Callable[..., CurrentUser]:
    """Candidates (acting for themselves), or staff holding any of these permissions."""
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.is_candidate or any(current_user.can(p) for p in perms):
            return current_user
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return _check


def require_roles(*roles: UserRole):
    """Role-based access dependency (kept for candidate-only routes)."""
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in [r.value for r in roles]:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return current_user
    return _check


async def verify_internal_secret(x_internal_secret: str = Header(...)):
    """Gate for service-to-service routes — no user JWT, just a shared secret."""
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Invalid internal secret.")
        


# Convenience dependencies
StudentOnly = Depends(require_roles(UserRole.STUDENT))
