from uuid import UUID
from .organization_db import OrganizationBase
from datetime import datetime
from organization.organization_settings import OrganizationSettingsBase

class OrganizationGet(OrganizationBase):
    id: UUID
    created_at: datetime


class OrganizationPatch(OrganizationBase):
    pass

class OrganizationPut(OrganizationBase):
    pass


class OrganizationSettingsGet(OrganizationSettingsBase):
    id: UUID


class OrganisationSettingsEdit(OrganizationSettingsBase):
    pass