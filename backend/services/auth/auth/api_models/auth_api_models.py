from pydantic import BaseModel
from auth.database.schema.organization.organization_api_models import OrganizationCreate

class SignUp(BaseModel):
    organization: OrganizationCreate