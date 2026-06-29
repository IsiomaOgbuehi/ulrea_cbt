from enum import Enum


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"
    SUPERVISOR = "supervisor"
    STAFF = "staff"

class AttemptStatus(str, Enum):
    STARTED = 'started'
    SUBMITTED = 'submitted'
    SCORED = 'scored'
