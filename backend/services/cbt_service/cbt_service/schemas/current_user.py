"""The signed-in user, as read from the access token (shared by every route)."""
from uuid import UUID

from pydantic import BaseModel

from cbt_service.core.permissions import Grants, P, Reach, decode, expand, grants_for_role
from cbt_service.database.models.enums.enums import UserRole


class CurrentUser(BaseModel):
    id: UUID
    org_id: UUID
    role: UserRole
    email: str | None = None
    verified: bool = False
    # Permissions from the token ({"exams.view": "assigned", ...}). Absent on
    # tokens issued before roles were editable: then the role's defaults apply.
    perms: dict[str, str] | None = None

    def grants(self) -> Grants:
        decoded: Grants | None = decode(self.perms)
        if self.role == UserRole.SUPER_ADMIN:
            return grants_for_role("super_admin")   # never restricted
        return expand(decoded) if decoded is not None else grants_for_role(self.role.value)

    def can(self, perm: P) -> bool:
        return perm in self.grants()

    def reach(self, perm: P) -> Reach | None:
        """"all" | "assigned" | "own", or None without the permission."""
        return self.grants().get(perm)

    @property
    def is_candidate(self) -> bool:
        return self.role == UserRole.STUDENT
