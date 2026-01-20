from enum import Enum

class AuthRoutes(Enum):
    BASE_ROUTE = '/auth'
    API_VERSION = '/api/v1'
    TOKEN = '/token'
    LOGIN = '/login'
    SIGNUP = '/signup'