from uuid import UUID
from .organization_db import OrganizationBase
from datetime import datetime
from .organization_settings import OrganizationSettingsBase

class OrganizationRead(OrganizationBase):
    id: UUID
    created_at: datetime

class OrganizationCreate(OrganizationBase):
    pass

class OrganizationUpdate(OrganizationBase):
    pass


class OrganizationSettingsRead(OrganizationSettingsBase):
    id: UUID


class OrganisationSettingsUpdate(OrganizationSettingsBase):
    pass