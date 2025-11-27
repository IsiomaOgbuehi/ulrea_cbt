from pydantic import BaseModel
from .token import Token
from .user import User

class LoginResponse(BaseModel):
    token: Token
    user: User