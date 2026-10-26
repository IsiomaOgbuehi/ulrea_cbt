"""
A person can belong to several organisations (e.g. teaches at two schools).
Each token is for one of them; switching issues tokens for another.

The last organisation used is remembered (Redis, per user) so signing in
again lands where they left off.
"""
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, col, select

from auth.api_models.login_response import LoginResponse
from auth.api_models.user_api_models import StaffUserResponse
from auth.api_models.workspace_api_models import WorkspaceRead
from auth.database.schema import OrganizationModel, OrganizationRead, UserModel
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus
from auth.services.role_service import RoleService
from auth.utility.jwt.jwt import create_access_token, create_refresh_token
from auth.utility.redis.redis_client import redis_client

LAST_ORG_TTL_SECONDS = 60 * 60 * 24 * 180   # forget after ~6 months unused


def _last_org_key(user_id: UUID) -> str:
    return f"last_org:{user_id}"


class WorkspaceService:

    # ------------------------------------------------------------- reading

    @staticmethod
    def active_memberships(
        session: Session, user_id: UUID
    ) -> list[tuple[OrgMembership, OrganizationModel]]:
        """Active memberships with their organisations, oldest first (stable order)."""
        rows = session.exec(
            select(OrgMembership, OrganizationModel)
            .join(OrganizationModel, col(OrganizationModel.id) == col(OrgMembership.org_id))
            .where(
                OrgMembership.user_id == user_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
            .order_by(col(OrgMembership.created_at))
        ).all()
        return [(m, o) for m, o in rows]

    @staticmethod
    def list_for_user(session: Session, user_id: UUID, current_org_id: UUID) -> list[WorkspaceRead]:
        out: list[WorkspaceRead] = []
        for membership, org in WorkspaceService.active_memberships(session, user_id):
            mine = RoleService.my_permissions(session, membership)
            out.append(WorkspaceRead(
                org_id=org.id,
                name=org.name,
                slug=org.slug,
                organization_type=getattr(org.organization_type, "value", org.organization_type),
                logo_url=org.logo_url,
                role=mine.role,
                role_name=mine.role_name,
                is_current=org.id == current_org_id,
            ))
        return out

    @staticmethod
    def membership_in(
        session: Session, user_id: UUID, org_id: UUID
    ) -> tuple[OrgMembership, OrganizationModel]:
        """The person's active membership in that organisation, or 403."""
        row = session.exec(
            select(OrgMembership, OrganizationModel)
            .join(OrganizationModel, col(OrganizationModel.id) == col(OrgMembership.org_id))
            .where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=403, detail="You're not an active member of that organisation.")
        return row[0], row[1]

    @staticmethod
    def pick(
        rows: list[tuple[OrgMembership, OrganizationModel]], preferred_org_id: UUID | None
    ) -> tuple[OrgMembership, OrganizationModel] | None:
        """The preferred organisation when they're still in it, else the first."""
        if not rows:
            return None
        if preferred_org_id is not None:
            for m, o in rows:
                if m.org_id == preferred_org_id:
                    return m, o
        return rows[0]

    # ------------------------------------------------------------ tokens

    @staticmethod
    def session_response(
        session: Session,
        user: UserModel,
        membership: OrgMembership,
        org: OrganizationModel,
        requires_setup: bool = False,
    ) -> LoginResponse:
        """Access + refresh tokens (both tied to this organisation) and who/where they are."""
        access = create_access_token(
            user.id, membership.org_id, membership.role,
            perms=RoleService.token_perms(session, membership),
        )
        return LoginResponse(
            access_token=access.access_token,
            refresh_token=create_refresh_token(user.id, org_id=membership.org_id),
            user=StaffUserResponse.model_validate(user, from_attributes=True),
            organization=OrganizationRead.model_validate(org, from_attributes=True),
            requires_setup=requires_setup,
        )

    # -------------------------------------------------- last organisation

    @staticmethod
    async def remember(user_id: UUID, org_id: UUID) -> None:
        try:
            await redis_client.set(_last_org_key(user_id), str(org_id), ex=LAST_ORG_TTL_SECONDS)
        except Exception:
            pass   # a convenience; never block sign-in on it

    @staticmethod
    async def preferred(user_id: UUID) -> UUID | None:
        try:
            raw = await redis_client.get(_last_org_key(user_id))
            return UUID(str(raw)) if raw else None
        except Exception:
            return None
