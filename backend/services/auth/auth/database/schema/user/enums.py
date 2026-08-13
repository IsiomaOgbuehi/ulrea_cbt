from enum import Enum

class UserRole(str, Enum):
    SUPER_ADMIN = 'super_admin'
    ADMIN = 'admin'
    TEACHER = 'teacher'
    STUDENT = 'student'
    SUPERVISOR = 'supervisor'
    STAFF = 'staff'


class MembershipStatus(str, Enum):
    PENDING = 'pending'
    ACTIVE = 'active'
    ARCHIVED = 'archived'   # email-verified users — preserved, just inactive
    REMOVED = 'removed'     # non-email users — effectively deleted from org

class VerificationMethod(str, Enum):
    EMAIL_OTP = "email_otp"    # standard signup/staff flow
    ACCESS_CODE = "access_code"  # student flow — no email needed



# NOT USED IN DB
class DeleteAction(str, Enum):
    DELETED = 'deleted'
    MEMBERSHIP_REMOVED = 'membership_removed'