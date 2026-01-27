from pydantic import BaseModel
from .token import Token, TokenData
from .user import User

class LoginResponse(BaseModel):
    token: Token
    user: User