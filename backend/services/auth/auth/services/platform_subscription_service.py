# auth/services/platform_subscription_service.py
import uuid
from uuid import UUID
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import Session, select, func

from auth.database.schema.platform_subscription.platform_subscription_db import (
    PlatformPlan, OrgPlatformSubscription,
    PlatformPlanStatus, OrgSubscriptionStatus
)
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.utility.payment.paystack import paystack
import logging

from auth.api_models.platform_subscription import OrgLimitsCheck
from auth.database.schema.membership.membership_db import OrgMembership


class PlatformSubscriptionService:

    # --------------------------------------------------------
    # PLAN MANAGEMENT (platform admin only)
    # --------------------------------------------------------

    @staticmethod
    def create_plan(
        session: Session,
        name: str,
        price: float,
        currency: str,
        interval: str,
        max_students: int | None,
        max_staff: int | None = None,
        max_exams: int | None = None,
        trial_days: int = 0,
        description: str | None = None,
        features: dict | None = None,
    ) -> PlatformPlan:
        plan = PlatformPlan(
            name=name,
            description=description,
            price=price,
            currency=currency,
            interval=interval,
            max_students=max_students,
            max_staff=max_staff,
            max_exams=max_exams,
            trial_days=trial_days,
            features=features or {},
        )
        session.add(plan)
        session.commit()
        session.refresh(plan)
        return plan

    @staticmethod
    def get_all_plans(session: Session) -> list[PlatformPlan]:
        return session.exec(
            select(PlatformPlan).where(
                PlatformPlan.status == PlatformPlanStatus.ACTIVE
            )
        ).all()

    # --------------------------------------------------------
    # ORG SUBSCRIPTION
    # --------------------------------------------------------

    @staticmethod
    def get_active_subscription(
        session: Session,
        org_id: UUID,
    ) -> OrgPlatformSubscription | None:
        return session.exec(
            select(OrgPlatformSubscription).where(
                OrgPlatformSubscription.org_id == org_id,
                OrgPlatformSubscription.status.in_([
                    OrgSubscriptionStatus.TRIAL,
                    OrgSubscriptionStatus.ACTIVE,
                ])
            )
        ).first()

    @staticmethod
    def start_trial(
        session: Session,
        org_id: UUID,
        plan_id: UUID,
    ) -> OrgPlatformSubscription:
        """
        Auto-called on org signup if plan has trial_days > 0.
        """
        plan = session.exec(
            select(PlatformPlan).where(PlatformPlan.id == plan_id)
        ).first()

        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found.")

        # Check not already subscribed
        existing = PlatformSubscriptionService.get_active_subscription(session, org_id)
        if existing:
            raise HTTPException(
                status_code=409,
                detail="Organization already has an active subscription or trial."
            )

        now = datetime.now(timezone.utc)
        trial_ends = now + timedelta(days=plan.trial_days) if plan.trial_days else now

        subscription = OrgPlatformSubscription(
            org_id=org_id,
            plan_id=plan_id,
            status=OrgSubscriptionStatus.TRIAL,
            is_trial=True,
            trial_ends_at=trial_ends,
            max_students=plan.max_students,
            max_staff=plan.max_staff,
            currency=plan.currency,
        )
        session.add(subscription)
        session.commit()
        session.refresh(subscription)
        return subscription

    @staticmethod
    async def initiate_payment(
        session: Session,
        org: OrganizationModel,
        plan_id: UUID,
        callback_url: str,
        paying_user_email: str,
    ) -> dict:
        plan = session.exec(
            select(PlatformPlan).where(
                PlatformPlan.id == plan_id,
                PlatformPlan.status == PlatformPlanStatus.ACTIVE,
            )
        ).first()

        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found.")

        reference = f"plt_{uuid.uuid4().hex[:16]}"
        amount_kobo = int(plan.price * 100)  # works for NGN; adjust for USD

        # Create pending subscription
        existing = PlatformSubscriptionService.get_active_subscription(
            session, org.id
        )

        now = datetime.now(timezone.utc)

        if existing:
            # Upgrading or renewing — update existing
            existing.plan_id = plan_id
            existing.paystack_reference = reference
            existing.status = OrgSubscriptionStatus.TRIAL \
                if existing.is_trial else existing.status
            existing.updated_at = now
            session.add(existing)
        else:
            existing = OrgPlatformSubscription(
                org_id=org.id,
                plan_id=plan_id,
                status=OrgSubscriptionStatus.TRIAL,
                is_trial=True if plan.trial_days > 0 else False,
                trial_ends_at=now + timedelta(days=plan.trial_days)
                    if plan.trial_days else None,
                max_students=plan.max_students,
                max_staff=plan.max_staff,
                paystack_reference=reference,
                currency=plan.currency,
            )
            session.add(existing)

        session.commit()

        paystack_resp = await paystack.initialize_payment(
            email=paying_user_email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata={
                "org_id": str(org.id),
                "plan_id": str(plan_id),
                "plan_name": plan.name,
                "subscription_id": str(existing.id),
            },
        )

        return {
            "payment_url": paystack_resp["data"]["authorization_url"],
            "reference": reference,
            "plan": plan.name,
            "amount": plan.price,
            "currency": plan.currency,
            "interval": plan.interval,
            "trial_days": plan.trial_days,
        }

    @staticmethod
    async def handle_webhook(
        session: Session,
        event: str,
        data: dict,
    ) -> None:
        if event != "charge.success":
            return

        reference = data.get("reference", "")
        transaction_id = str(data.get("id", ""))

        if not reference.startswith("plt_"):
            return  # not a platform subscription payment

        subscription = session.exec(
            select(OrgPlatformSubscription).where(
                OrgPlatformSubscription.paystack_reference == reference,
            )
        ).first()

        if not subscription:
            logging.warning("Platform subscription not found for ref %s", reference)
            return

        verification = await paystack.verify_payment(reference)
        if verification["data"]["status"] != "success":
            return

        plan = session.exec(
            select(PlatformPlan).where(PlatformPlan.id == subscription.plan_id)
        ).first()

        now = datetime.now(timezone.utc)
        period_end = PlatformSubscriptionService._calculate_period_end(
            plan.interval, now
        )

        subscription.status = OrgSubscriptionStatus.ACTIVE
        subscription.is_trial = False
        subscription.paystack_transaction_id = transaction_id
        subscription.current_period_start = now
        subscription.current_period_end = period_end
        subscription.amount_paid = plan.price
        subscription.updated_at = now
        session.add(subscription)
        session.commit()

    @staticmethod
    def _calculate_period_end(interval: str, from_date: datetime) -> datetime:
        mapping = {
            "daily": timedelta(days=1),
            "weekly": timedelta(weeks=1),
            "monthly": timedelta(days=30),
            "quarterly": timedelta(days=90),
            "yearly": timedelta(days=365),
            "lifetime": timedelta(days=36500),  # 100 years
        }
        return from_date + mapping.get(interval, timedelta(days=30))

    # --------------------------------------------------------
    # LIMITS CHECK — called before adding students/staff
    # --------------------------------------------------------

    @staticmethod
    def check_org_limits(
        session: Session,
        org_id: UUID,
    ) -> OrgLimitsCheck:

        subscription = PlatformSubscriptionService.get_active_subscription(
            session, org_id
        )

        # Count current students and staff
        current_students = session.exec(
            select(func.count(OrgMembership.id)).where(
                OrgMembership.org_id == org_id,
                OrgMembership.role == UserRole.STUDENT,
            )
        ).one()

        current_staff = session.exec(
            select(func.count(OrgMembership.id)).where(
                OrgMembership.org_id == org_id,
                OrgMembership.role.in_([
                    UserRole.ADMIN,
                    UserRole.TEACHER,
                    UserRole.SUPERVISOR,
                ])
            )
        ).one()

        plan_name = "No Plan"
        sub_status = OrgSubscriptionStatus.TRIAL
        max_students = None
        max_staff = None

        if subscription:
            plan = session.exec(
                select(PlatformPlan).where(
                    PlatformPlan.id == subscription.plan_id
                )
            ).first()
            plan_name = plan if plan.name else "Unknown"
            sub_status = subscription.status
            max_students = subscription.max_students
            max_staff = subscription.max_staff

            # Check trial expiry
            if (
                subscription.is_trial
                and subscription.trial_ends_at
                and subscription.trial_ends_at < datetime.now(timezone.utc)
            ):
                subscription.status = OrgSubscriptionStatus.EXPIRED
                session.add(subscription)
                session.commit()
                sub_status = OrgSubscriptionStatus.EXPIRED

        can_add_students = (
            sub_status in (OrgSubscriptionStatus.TRIAL, OrgSubscriptionStatus.ACTIVE)
            and (max_students is None or current_students < max_students)
        )

        can_add_staff = (
            sub_status in (OrgSubscriptionStatus.TRIAL, OrgSubscriptionStatus.ACTIVE)
            and (max_staff is None or current_staff < max_staff)
        )

        return OrgLimitsCheck(
            can_add_students=can_add_students,
            can_add_staff=can_add_staff,
            current_students=current_students,
            max_students=max_students,
            students_remaining=(
                max_students - current_students
                if max_students is not None else None
            ),
            current_staff=current_staff,
            max_staff=max_staff,
            staff_remaining=(
                max_staff - current_staff
                if max_staff is not None else None
            ),
            plan_name=plan_name,
            subscription_status=sub_status,
        )

    @staticmethod
    def assert_can_add_student(session: Session, org_id: UUID) -> None:
        """Call before creating a student. Raises 403 if limit exceeded."""
        limits = PlatformSubscriptionService.check_org_limits(session, org_id)

        if not limits.can_add_students:
            if limits.subscription_status == "none":
                raise HTTPException(
                    status_code=403,
                    detail="Your organization has no active subscription. "
                           "Please subscribe to add students."
                )
            if limits.subscription_status == OrgSubscriptionStatus.EXPIRED:
                raise HTTPException(
                    status_code=403,
                    detail="Your subscription has expired. Please renew to add students."
                )
            raise HTTPException(
                status_code=403,
                detail=f"Student limit reached ({limits.current_students}/"
                       f"{limits.max_students}). "
                       f"Upgrade your plan to add more students."
            )

    @staticmethod
    def assert_can_add_staff(session: Session, org_id: UUID) -> None:
        """Call before creating staff. Raises 403 if limit exceeded."""
        limits = PlatformSubscriptionService.check_org_limits(session, org_id)

        if not limits.can_add_staff:
            raise HTTPException(
                status_code=403,
                detail=f"Staff limit reached. Upgrade your plan to add more staff."
            )