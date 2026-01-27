from pydantic import BaseModel
from datetime import datetime

class Token(BaseModel):
    access_token: str
    jti: str
    expires_at: datetime

class TokenData(BaseModel):
    access_token: str
    token_type: str | None = 'bearer'