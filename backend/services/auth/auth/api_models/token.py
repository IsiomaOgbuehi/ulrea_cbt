from pydantic import BaseModel
from datetime import datetime

class Token(BaseModel):
    access_token: str
    jti: str
    expires_at: datetime

class TokenData(BaseModel):
    access_token: str
    token_type: str | None = 'bearer'

class RefreshRequest(BaseModel):
    refresh_token: str

class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class TokenBase(BaseModel):
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"