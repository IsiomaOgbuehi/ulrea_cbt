# auth/api_models/student_signup.py
from pydantic import BaseModel, EmailStr
from uuid import UUID
from auth.database.schema.user.enums import UserRole


class StudentSelfSignup(BaseModel):
    """
    Student signs themselves up — no org context needed at signup.
    They can subscribe to orgs after account creation.
    """
    firstname: str
    lastname: str
    othername: str | None = None
    email: EmailStr
    phone: str | None = None
    password: str
    confirm_password: str


class OrgSubscribeRequest(BaseModel):
    """Student subscribes to a public exam body."""
    org_id: UUID


class OrgSearchQuery(BaseModel):
    query: str | None = None
    category: str | None = None     # OrganizationCategory filter
    page: int = 1
    per_page: int = 20


class PublicOrgRead(BaseModel):
    """What a student sees when browsing public organizations."""
    id: UUID
    name: str
    slug: str
    description: str | None
    organization_type: str
    logo_url: str | None
    website: str | None
    verified: bool
    allow_self_subscription: bool
    member_count: int | None = None


class AddExistingUserToOrgRequest(BaseModel):
    """Org adds a user who already exists globally by email."""
    email: EmailStr
    role: UserRole
    institution_id: str | None = None
    send_notification: bool = True