from enum import Enum


class MembershipJoinType(str, Enum):
    INVITED = "invited"             # org created the user
    SELF_JOINED = "self_joined"     # student subscribed themselves
    AUTO_ADDED = "auto_added"       # existing email user auto-added by org