from pydantic import BaseModel
from auth.database.schema import OrganizationCreate, OrganizationRead
from auth.database.schema import UserCreate, UserRead
from .token import TokenData

class SignUp(BaseModel):
    organization: OrganizationCreate
    user: UserCreate

class SignUpResponse(BaseModel):
    organization: OrganizationRead
    user: UserRead
    token: TokenData