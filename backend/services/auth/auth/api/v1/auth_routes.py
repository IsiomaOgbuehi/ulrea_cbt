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
    INIT_STAFF = '/init/staff'
    INIT_STUDENT = '/init/student'
    STUDENT_LOGIN = '/login/student'