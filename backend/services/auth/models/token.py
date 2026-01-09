from pydantic import BaseModel

class Token(BaseModel):
    access_token: str
    token_type: str | None = 'bearer'

class TokenData(BaseModel):
    username: str | None = None