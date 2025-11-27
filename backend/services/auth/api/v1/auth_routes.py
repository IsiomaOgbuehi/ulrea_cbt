from enum import Enum

class AuthRoutes(Enum):
    base_route = '/auth'
    api_version = '/api/v1'
    token = '/token'
    login = '/login'