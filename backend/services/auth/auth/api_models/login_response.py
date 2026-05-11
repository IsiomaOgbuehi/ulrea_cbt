from pydantic import BaseModel

from auth.database.schema.organization.organization_api_models import OrganizationRead
from auth.api_models.user_api_models import UserRead
from .token import Token, TokenBase, TokenData
from .user import User

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: object
    organization: OrganizationRead | None = None
    requires_setup: bool = False  # frontend redirects to /setup if True


class StudentFirstLoginResponse(TokenBase):
    detail: str

class StaffActivateResponse(TokenBase):
    detail: str

class StaffFirstLoginResponse(TokenBase):
    detail: str