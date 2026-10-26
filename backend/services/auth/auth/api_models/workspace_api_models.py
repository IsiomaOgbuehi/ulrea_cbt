"""Organisations a person belongs to, and switching between them."""
from uuid import UUID

from pydantic import BaseModel


class WorkspaceRead(BaseModel):
    """One organisation this person can work in."""
    org_id: UUID
    name: str
    slug: str | None = None
    organization_type: str | None = None
    logo_url: str | None = None
    role: str            # base role on the membership
    role_name: str       # e.g. "Teacher", "HR Officer"
    is_current: bool     # the organisation the calling token is for


class SwitchOrganizationRequest(BaseModel):
    org_id: UUID
    # The refresh token being replaced, so it can be revoked with the old access token.
    refresh_token: str | None = None
