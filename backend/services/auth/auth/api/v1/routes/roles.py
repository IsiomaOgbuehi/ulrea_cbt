from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from auth.api_models.role_api_models import (
    AssignRoleRequest, MyPermissionsRead, PermissionInfoRead, RoleCreate, RoleRead, RoleUpdate,
)
from auth.core.permissions import P
from auth.database.database import SessionDep
from auth.database.schema.user.enums import UserRole
from auth.dependencies.auth_dependencies import get_user_context, require_permission
from auth.services.role_service import RoleService
from auth.services.user.user_context import UserContext

router = APIRouter(tags=["roles & permissions"])


def _is_owner(ctx: UserContext) -> bool:
    return ctx.membership.role == UserRole.SUPER_ADMIN


''' PERMISSION CATALOG 📚 '''
@router.get("/roles/catalog", response_model=list[PermissionInfoRead])
def permission_catalog(ctx: UserContext = Depends(get_user_context)) -> list[PermissionInfoRead]:
    """Every permission, grouped and described, for building the role editor."""
    return RoleService.catalog()


''' MY PERMISSIONS 🔑 '''
@router.get("/roles/me", response_model=MyPermissionsRead)
def my_permissions(session: SessionDep, ctx: UserContext = Depends(get_user_context)) -> MyPermissionsRead:
    """The signed-in member's role and effective permissions (fresh from the database)."""
    return RoleService.my_permissions(session, ctx.membership)


''' LIST ROLES 🧑‍💼 '''
@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.ROLES_MANAGE, P.STAFF_VIEW)),
) -> list[RoleRead]:
    """Owner, built-in roles (with any edits) and custom roles, with member counts."""
    return RoleService.list(session, ctx.membership.org_id)


''' CREATE ROLE ➕ '''
@router.post("/roles", response_model=RoleRead, status_code=201)
def create_role(
    payload: RoleCreate,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.ROLES_MANAGE)),
) -> RoleRead:
    return RoleService.create(
        session, ctx.membership.org_id, payload, ctx.user.id, ctx.grants(), _is_owner(ctx)
    )


''' UPDATE ROLE ✏️ '''
@router.patch("/roles/{key}", response_model=RoleRead)
def update_role(
    key: str,
    payload: RoleUpdate,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.ROLES_MANAGE)),
) -> RoleRead:
    """Change a role's permissions (and name/description for custom roles)."""
    return RoleService.update(
        session, ctx.membership.org_id, key, payload, ctx.user.id, ctx.grants(), _is_owner(ctx)
    )


''' RESET BUILT-IN ROLE ↩️ '''
@router.post("/roles/{key}/reset", response_model=RoleRead)
def reset_role(
    key: str,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.ROLES_MANAGE)),
) -> RoleRead:
    return RoleService.reset(session, ctx.membership.org_id, key)


''' DELETE CUSTOM ROLE ❌ '''
@router.delete("/roles/{key}", status_code=204)
def delete_role(
    key: str,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.ROLES_MANAGE)),
) -> None:
    RoleService.delete(session, ctx.membership.org_id, key)


''' GIVE A STAFF MEMBER A ROLE 🎖️ '''
@router.put("/users/staff/{user_id}/role", response_model=MyPermissionsRead)
def assign_role(
    user_id: UUID,
    payload: AssignRoleRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_permission(P.STAFF_MANAGE)),
) -> MyPermissionsRead:
    """
    Applies to their next sign-in or token refresh in the exam service; the
    auth service uses it immediately.
    """
    if not payload.role:
        raise HTTPException(status_code=422, detail="Choose a role.")
    return RoleService.assign(
        session, ctx.membership.org_id, user_id, payload.role,
        ctx.user.id, ctx.membership.role, ctx.grants(),
    )
