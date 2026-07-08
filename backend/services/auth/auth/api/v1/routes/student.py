# auth/api/v1/routes/student.py
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from auth.database.database import SessionDep
from auth.dependencies.auth_dependencies import get_provisional_or_authenticated_user, get_user_context
from auth.database.schema.user.enums import MembershipStatus, UserRole
from auth.services.subscription_service import SubscriptionService
from auth.utility.otp.otp_service import OtpService
from auth.utility.email.email_service import EmailService
from auth.api_models.student_signup import (
    StudentSelfSignup, OrgSubscribeRequest, PublicOrgRead
)
from auth.utility.otp.otp_enums import OtpPurpose
from auth.core.settings import settings
from auth.services.user.user_context import UserContext
from auth.database.schema.user.user_db import UserModel
router = APIRouter(prefix="/student", tags=["student"])

IS_DEV = settings.ENVIRONMENT == "dev"


''' STUDENT SELF SIGNUP 🎓 '''
@router.post("/signup")
async def student_signup(
    payload: StudentSelfSignup,
    session: SessionDep,
):
    # No auth needed — public endpoint, no changes here
    user = SubscriptionService.student_self_signup(session, payload)

    try:
        otp = await OtpService.request_otp(
            purpose=OtpPurpose.SIGNUP,
            identifier=user.email,
        )
        await EmailService.send_otp_email(user.email, otp)
    except Exception:
        logging.exception("OTP send failed for self-signup %s", user.email)

    response = {
        "detail": "Account created. Check your email for a verification code.",
        "otp_sent_to": EmailService.mask_email(user.email),
    }

    if IS_DEV:
        response["otp"] = otp

    return response


''' DISCOVER PUBLIC ORGS 🔍 '''
@router.get("/organizations", response_model=dict)
async def discover_organizations(
    session: SessionDep,
    query: str | None = Query(default=None),
    category: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    user: UserModel = Depends(get_provisional_or_authenticated_user),  # ← accepts provisional token
    # ctx: UserContext = Depends(get_user_context),   # any authenticated user
):
    orgs, total = SubscriptionService.search_public_orgs(
        session=session,
        query=query,
        category=category,
        page=page,
        per_page=per_page,
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "organizations": [
            PublicOrgRead(
                id=org.id,
                name=org.name,
                slug=org.slug,
                description=org.description,
                organization_type=org.organization_type,
                logo_url=org.logo_url,
                website=org.website,
                verified=org.verified,
                allow_self_subscription=org.allow_self_subscription,
            )
            for org in orgs
        ],
    }


''' MY ORGANIZATIONS 🏫 '''
@router.get("/my-organizations")
async def my_organizations(
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
):
    memberships = SubscriptionService.get_my_organizations(session, ctx.user.id)

    return [
        {
            "membership_id": str(m.id),
            "join_type": m.join_type,
            "role": m.role,
            "joined_at": m.created_at,
            "organization": {
                "id": str(org.id),
                "name": org.name,
                "slug": org.slug,
                "organization_type": org.organization_type,
                "logo_url": org.logo_url,
                "verified": org.verified,
            }
        }
        for m, org in memberships
    ]


''' SUBSCRIBE TO ORG ✅ '''
@router.post("/organizations/subscribe")
async def subscribe_to_org(
    payload: OrgSubscribeRequest,
    session: SessionDep,
    user: UserModel = Depends(get_provisional_or_authenticated_user),  # ← accepts provisional token
    # ctx: UserContext = Depends(get_user_context),
):
    membership = SubscriptionService.subscribe_to_org(
        session=session,
        user=user,
        org_id=payload.org_id,
    )
    return {
        "detail": "Successfully subscribed.",
        "membership_id": str(membership.id),
        "org_id": str(membership.org_id),
    }


''' UNSUBSCRIBE FROM ORG ❌ '''
@router.delete("/organizations/{org_id}/unsubscribe", status_code=204)
async def unsubscribe_from_org(
    org_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
):
    SubscriptionService.unsubscribe_from_org(session, ctx.user, org_id)