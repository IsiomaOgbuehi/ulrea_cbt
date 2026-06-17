# auth/api/v1/routes/exam_subscriptions.py
import json
from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from auth.database.database import SessionDep
from auth.dependencies.auth_dependencies import get_user_context
from auth.database.schema.user.enums import UserRole
from auth.services.exam_subscription_service import ExamSubscriptionService
from auth.api_models.exam_subscription import (
    InitiateSubscriptionRequest,
    BulkSubscribeStudentsRequest,
    SubscriptionRead,
    ExamAccessCheck,
)
from auth.utility.payment.paystack.paystack import paystack
from .users import require_roles
from auth.services.user.user_context import UserContext
import logging

router = APIRouter(prefix="/subscriptions", tags=["exam-subscriptions"])


''' CHECK EXAM ACCESS 🔑 '''
@router.get("/access-check/{exam_body_org_id}", response_model=ExamAccessCheck)
async def check_exam_access(
    exam_body_org_id: UUID,
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
):
    can_access, reason = ExamSubscriptionService.can_access_exam_body(
        session=session,
        student_id=ctx.user.id,
        exam_body_org_id=exam_body_org_id,
    )
    return ExamAccessCheck(can_access=can_access, reason=reason)


''' STUDENT INITIATES PAYMENT 💳 '''
@router.post("/pay")
async def initiate_payment(
    payload: InitiateSubscriptionRequest,
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
):
    return await ExamSubscriptionService.initiate_student_payment(
        session=session,
        student=ctx.user,
        exam_body_org_id=payload.exam_body_org_id,
        callback_url=payload.callback_url,
    )


''' ORG PAYS FOR STUDENTS IN BULK 🏫 '''
@router.post("/org/bulk-pay")
async def org_bulk_payment(
    payload: BulkSubscribeStudentsRequest,
    session: SessionDep,
    ctx: UserContext = Depends(require_roles(UserRole.SUPER_ADMIN, UserRole.ADMIN)),
):
    """
    require_roles already returns a UserContext, so ctx here is fully typed.
    Pass ctx.user downstream where the old `creator: UserModel` was expected.
    """
    return await ExamSubscriptionService.initiate_org_bulk_payment(
        session=session,
        creator=ctx.user,                       # ← unwrap user from context
        exam_body_org_id=payload.exam_body_org_id,
        student_ids=payload.student_ids,
        callback_url=payload.callback_url,
    )


''' MY SUBSCRIPTIONS 📋 '''
@router.get("/me", response_model=list[SubscriptionRead])
async def my_subscriptions(
    session: SessionDep,
    ctx: UserContext = Depends(get_user_context),
):
    subs = ExamSubscriptionService.get_my_subscriptions(session, ctx.user.id)
    return [SubscriptionRead.model_validate(s, from_attributes=True) for s in subs]


''' PAYSTACK WEBHOOK 🔔 '''
@router.post("/webhook/paystack")
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    x_paystack_signature: str = Header(...),
):
    # No auth dependency — Paystack calls this directly, verified by signature
    body = await request.body()

    if not paystack.verify_webhook_signature(body, x_paystack_signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    try:
        payload = json.loads(body)
        await ExamSubscriptionService.handle_webhook(
            session=session,
            event=payload.get("event"),
            data=payload.get("data", {}),
        )
    except Exception:
        logging.exception("Paystack webhook processing error")

    return {"status": "ok"}