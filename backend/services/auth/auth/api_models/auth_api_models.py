from pydantic import BaseModel
from auth.database.schema import OrganizationCreate
from auth.database.schema import UserCreate

class SignUp(BaseModel):
    organization: OrganizationCreate
    user: UserCreate