from pydantic import BaseModel

from auth.database.schema.organization.organization_api_models import OrganizationRead
from auth.database.schema.user.user_api_models import UserRead
from .token import Token, TokenData
from .user import User

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserRead
    organization: OrganizationRead | None = None