from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.services.platform_subscription_service import PlatformSubscriptionService
from auth.api_models.platform_subscription import (
    PlatformPlanRead, SubscribeToPlatformRequest,
    OrgSubscriptionRead, OrgLimitsCheck
)
from auth.dependencies.auth_dependencies import get_user_context
from auth.services.user.user_context import UserContext
from .users import require_roles
from auth.utility.payment.paystack import paystack
from sqlmodel import select
import json
import logging

router = APIRouter(prefix="/platform", tags=["platform-subscriptions"])


''' LIST AVAILABLE PLANS 📋 '''
@router.get("/plans", response_model=list[PlatformPlanRead])
async def list_plans(session: SessionDep):
    """Public — anyone can see available plans before signing up."""
    plans = PlatformSubscriptionService.get_all_plans(session)
    return [PlatformPlanRead.model_validate(p, from_attributes=True) for p in plans]


''' START TRIAL (auto-called on signup) 🎁 '''
@router.post("/trial/{plan_id}")
async def start_trial(
    plan_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(
        require_roles(UserRole.SUPER_ADMIN)
    ),
):
    org = session.exec(
        select(OrganizationModel).where(
            OrganizationModel.id == ctx.membership.org_id
        )
    ).first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    subscription = PlatformSubscriptionService.start_trial(
        session=session,
        org_id=org.id,
        plan_id=plan_id,
    )

    return {
        "detail": "Trial started.",
        "trial_ends_at": subscription.trial_ends_at,
        "plan_id": str(plan_id),
    }


''' SUBSCRIBE / UPGRADE 💳 '''
@router.post("/subscribe")
async def subscribe(
    payload: SubscribeToPlatformRequest,
    session: SessionDep,
    ctx: UserContext = Depends(
        require_roles(UserRole.SUPER_ADMIN)
    ),
):
    """Organization subscribes or upgrades their plan."""
    org = session.exec(
        select(OrganizationModel).where(
            OrganizationModel.id == ctx.membership.org_id
        )
    ).first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found.")

    return await PlatformSubscriptionService.initiate_payment(
        session=session,
        org=org,
        plan_id=payload.plan_id,
        callback_url=payload.callback_url,
        paying_user_email=ctx.user.email,
    )


''' MY SUBSCRIPTION 📊 '''
@router.get("/my-subscription")
async def my_subscription(
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    subscription = PlatformSubscriptionService.get_active_subscription(
        session, ctx.membership.org_id
    )
    if not subscription:
        return {"status": "none", "detail": "No active subscription."}

    from auth.database.schema.platform_subscription.platform_subscription_db import PlatformPlan
    plan = session.exec(
        select(PlatformPlan).where(PlatformPlan.id == subscription.plan_id)
    ).first()

    return {
        "status": subscription.status,
        "is_trial": subscription.is_trial,
        "trial_ends_at": subscription.trial_ends_at,
        "current_period_end": subscription.current_period_end,
        "plan": PlatformPlanRead.model_validate(
            plan, from_attributes=True
        ) if plan else None,
    }


''' CHECK LIMITS 📏 '''
@router.get("/limits", response_model=OrgLimitsCheck)
async def check_limits(
    session: SessionDep,
    ctx: UserContext = Depends(
        require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)
    ),
):
    """Check how many students/staff org can still add."""
    return PlatformSubscriptionService.check_org_limits(
        session, ctx.membership.org_id
    )


''' PAYSTACK WEBHOOK 🔔 '''
@router.post("/webhook/paystack")
async def platform_webhook(
    request: Request,
    session: SessionDep,
    x_paystack_signature: str = Header(...),
):
    body = await request.body()

    if not paystack.verify_webhook_signature(body, x_paystack_signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    try:
        payload = json.loads(body)
        await PlatformSubscriptionService.handle_webhook(
            session=session,
            event=payload.get("event"),
            data=payload.get("data", {}),
        )
    except Exception:
        logging.exception("Platform subscription webhook error")

    return {"status": "ok"}