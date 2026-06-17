from uuid import UUID

from pydantic import BaseModel, EmailStr

from auth.database.schema.organization.enums import OrganizationType, OrganizationVisibility
from .organization_db import OrganizationBase
from datetime import datetime
from .organization_settings import OrganizationSettingsBase

class OrganizationRead(BaseModel):
    id: UUID
    name: str
    address: str
    email: EmailStr
    phone: str
    organization_type: OrganizationType
    slug: str | None = None
    logo_url: str | None = None
    website: str | None = None
    description: str | None = None
    visibility: str
    allow_self_subscription: bool = False
    require_subscription_approval: bool = False
    exam_body_trial_days: int = 0
    created_at: datetime

class OrganizationCreate(BaseModel):
    name: str
    address: str
    email: EmailStr
    phone: str
    organization_type: OrganizationType = OrganizationType.SCHOOL
    slug: str | None = None  # e.g. "waec-ng" for search/URL
    logo_url: str | None = None
    website: str | None = None
    description: str | None = None
    visibility: str = OrganizationVisibility.PRIVATE
    allow_self_subscription: bool = False
    require_subscription_approval: bool = False
    exam_body_trial_days: int = 0

class OrganizationUpdate(OrganizationCreate):
    pass


class OrganizationSettingsRead(OrganizationSettingsBase):
    id: UUID


class OrganisationSettingsUpdate(OrganizationSettingsBase):
    pass