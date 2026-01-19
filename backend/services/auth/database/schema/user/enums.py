from enum import Enum

class UserRole(str, Enum):
    SUPER_ADMIN = 'super_admin'
    ADMIN = 'admin'
    TEACHER = 'teacher'
    STUDENT = 'student'
    SUPERVISOR = 'supervisor'
