from dataclasses import dataclass

from auth.core.permissions import Grants, P, Reach, grants_for_role
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import UserRole
from auth.database.schema.user.user_db import UserModel


@dataclass
class UserContext:
    user: UserModel
    membership: OrgMembership
    # Filled by get_user_context from the organisation's roles (RoleService).
    resolved_grants: Grants | None = None

    def grants(self) -> Grants:
        """What this member may do: their org role's permissions, else the defaults."""
        if self.resolved_grants is not None:
            return self.resolved_grants
        role: UserRole = self.membership.role
        return grants_for_role(role.value if isinstance(role, UserRole) else str(role))

    def can(self, perm: P) -> bool:
        return perm in self.grants()

    def reach(self, perm: P) -> Reach | None:
        """"all" | "assigned" | "own", or None without the permission."""
        return self.grants().get(perm)

    @property
    def is_candidate(self) -> bool:
        return self.membership.role == UserRole.STUDENT
