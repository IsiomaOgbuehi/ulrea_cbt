from dataclasses import dataclass

from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.user_db import UserModel


@dataclass
class UserContext:
    user: UserModel
    membership: OrgMembership