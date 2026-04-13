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
    # token: TokenData
    otp_required: bool = True        # frontend uses this to route to OTP screen
    otp_sent_to: str
    otp: str | None = None # only populated in dev, stripped in prod 