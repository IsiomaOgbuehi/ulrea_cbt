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
    CREATE_STAFF = '/staff/create'
    CREATE_STUDENT = '/students/create'
    CREATE_STUDENTS_BULK = '/students/create/bulk'
    STUDENT_BULK_TEMPLATE = '/students/create/bulk/template'
    INIT_STAFF = '/staff/init'
    STAFF_ACTIVATE = '/staff/activate'
    STAFF_ACTIVATE_RESEND = '/staff/activate/resend'
    INIT_STUDENT = '/student/init'
    STUDENT_LOGIN = '/student/login'
    STUDENT_LOGIN_QUESTION = '/student/login/question'
    FORGOT_PASSWORD = '/forgot-password'
    RESET_PASSWORD = '/reset-password'
    STUDENT_VERIFY_FORGOT_PASSWORD = '/students/forgot-password/verify'
    STUDENT_RESET_PASSWORD_QA = '/students/forgot-password/reset'