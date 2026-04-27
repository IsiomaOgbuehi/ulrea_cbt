from enum import Enum


class ExamStatus(str, Enum):
    DRAFT = "draft"                   # teacher building it
    PENDING_APPROVAL = "pending"      # submitted to admin for review
    APPROVED = "approved"             # admin approved, not yet started
    ACTIVE = "active"                 # currently running
    CLOSED = "closed"                 # past end_time
    REJECTED = "rejected"             # admin rejected, back to teacher


class AssignmentStatus(str, Enum):
    ASSIGNED = "assigned"             # student assigned but not started
    STARTED = "started"               # student opened exam
    SUBMITTED = "submitted"           # student submitted
    MISSED = "missed"                 # past deadline, never started


class UserRole(str, Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    TEACHER = "teacher"
    STUDENT = "student"
    SUPERVISOR = "supervisor"
