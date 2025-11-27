from fastapi import HTTPException

class AuthException(HTTPException):
    message: str | None = 'Unauthorized'