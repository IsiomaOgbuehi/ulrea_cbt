from enum import Enum

class AuthRoutes(Enum):
    BASE_ROUTE = '/auth'
    API_VERSION = '/api/v1'
    TOKEN = '/token'
    LOGIN = '/login'
    SIGNUP = '/signup'
    LOGOUT = '/logout'
    REQUEST_OTP = '/otp/request'
    VERIFY_OTP = '/otp/verify'
    REFRESH_TOKEN = '/token/refresh'

    # lives in users.py router
    CREATE_STAFF = '/create/staff'
    CREATE_STUDENT = '/create/students'
    CREATE_STUDENTS_BULK = '/create/students/bulk'
    STUDENT_BULK_TEMPLATE = '/create/students/bulk/template'
    INIT_STAFF = '/init/staff'
    STAFF_ACTIVATE = '/staff/activate'
    INIT_STUDENT = '/init/student'
    STUDENT_LOGIN = '/login/student'
    STUDENT_LOGIN_QUESTION = '/login/student/question'