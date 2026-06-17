# auth/services/exam_subscription_service.py
import uuid
from uuid import UUID
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException
from sqlmodel import Session, select

from auth.database.schema.exam_subscription.exam_subscription_db import (
    ExamBodySubscription, BulkOrgSubscription,
    SubscriptionPlan, SubscriptionStatus, SubscribedBy
)
from auth.database.schema.organization.organization_db import OrganizationModel, OrganizationVisibility
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole
from auth.utility.payment.paystack import paystack


class ExamSubscriptionService:

    # --------------------------------------------------------
    # ACCESS CHECK — called before serving an exam
    # --------------------------------------------------------

    @staticmethod
    def can_access_exam_body(
        session: Session,
        student_id: UUID,
        exam_body_org_id: UUID,
        auto_start_trial: bool = True,
    ) -> tuple[bool, str | None, bool]:
        """Returns (can_access, reason, trial_started)."""
        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == exam_body_org_id
            )
        ).first()

        if not org:
            return False, "Organization not found.", False

        if org.subscription_plan == SubscriptionPlan.FREE:
            return True, None, False

        # Check existing subscription
        subscription = session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.student_id == student_id,
                ExamBodySubscription.exam_body_org_id == exam_body_org_id,
                ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
            )
        ).first()

        if subscription:
            if subscription.expires_at and \
                    subscription.expires_at < datetime.now(timezone.utc):
                subscription.status = SubscriptionStatus.EXPIRED
                session.add(subscription)
                session.commit()
            else:
                return True, None, False

        # Auto-start trial if eligible
        if auto_start_trial:
            student = session.exec(
                select(UserModel).where(UserModel.id == student_id)
            ).first()
            if student:
                trial = ExamSubscriptionService.start_exam_body_trial(
                    session, student, exam_body_org_id
                )
                if trial:
                    days_left = (
                        trial.trial_ends_at - datetime.now(timezone.utc)
                    ).days
                    return True, f"Trial active — {days_left} days remaining.", True

        return False, "An active subscription is required.", False

    # --------------------------------------------------------
    # STUDENT SELF-PAYMENT
    # --------------------------------------------------------

    @staticmethod
    async def initiate_student_payment(
        session: Session,
        student: UserModel,
        exam_body_org_id: UUID,
        callback_url: str,
    ) -> dict:
        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == exam_body_org_id,
                OrganizationModel.visibility == OrganizationVisibility.PUBLIC,
            )
        ).first()

        if not org:
            raise HTTPException(status_code=404, detail="Exam body not found.")

        if org.subscription_plan == SubscriptionPlan.FREE:
            raise HTTPException(
                status_code=400,
                detail="This exam body is free. No payment required."
            )

        if not org.subscription_price:
            raise HTTPException(
                status_code=400,
                detail="Subscription price not configured for this exam body."
            )

        # Check not already subscribed
        existing = session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.student_id == student.id,
                ExamBodySubscription.exam_body_org_id == exam_body_org_id,
                ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
            )
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="You already have an active subscription."
            )

        reference = f"sub_{uuid.uuid4().hex[:16]}"
        amount_kobo = int(org.subscription_price * 100)

        # Create pending subscription record
        subscription = ExamBodySubscription(
            student_id=student.id,
            exam_body_org_id=exam_body_org_id,
            plan=SubscriptionPlan.PAID,
            status=SubscriptionStatus.PENDING,
            subscribed_by=SubscribedBy.SELF,
            paystack_reference=reference,
            amount_paid=org.subscription_price,
            currency=org.subscription_currency,
        )
        session.add(subscription)
        session.commit()

        # Initialize Paystack payment
        paystack_response = await paystack.initialize_payment(
            email=student.email,
            amount_kobo=amount_kobo,
            reference=reference,
            callback_url=callback_url,
            metadata={
                "student_id": str(student.id),
                "exam_body_org_id": str(exam_body_org_id),
                "subscription_id": str(subscription.id),
            },
        )

        return {
            "payment_url": paystack_response["data"]["authorization_url"],
            "reference": reference,
            "amount": org.subscription_price,
            "currency": org.subscription_currency,
        }

    # --------------------------------------------------------
    # ORG BULK PAYMENT FOR STUDENTS
    # --------------------------------------------------------

    @staticmethod
    async def initiate_org_bulk_payment(
        session: Session,
        creator: UserModel,
        exam_body_org_id: UUID,
        student_ids: list[UUID],
        callback_url: str,
    ) -> dict:
        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == exam_body_org_id,
                OrganizationModel.visibility == OrganizationVisibility.PUBLIC,
            )
        ).first()

        if not org:
            raise HTTPException(status_code=404, detail="Exam body not found.")

        if org.subscription_plan == SubscriptionPlan.FREE:
            raise HTTPException(
                status_code=400,
                detail="This exam body is free. No payment needed."
            )

        if not org.subscription_price:
            raise HTTPException(
                status_code=400,
                detail="Subscription price not configured."
            )

        # Validate all students belong to org
        valid_student_ids = []
        for student_id in student_ids:
            student = session.exec(
                select(UserModel).where(
                    UserModel.id == student_id,
                    UserModel.org_id == creator.org_id,
                    UserModel.role == UserRole.STUDENT,
                )
            ).first()
            if student:
                # Skip already subscribed
                already = session.exec(
                    select(ExamBodySubscription).where(
                        ExamBodySubscription.student_id == student_id,
                        ExamBodySubscription.exam_body_org_id == exam_body_org_id,
                        ExamBodySubscription.status == SubscriptionStatus.ACTIVE,
                    )
                ).first()
                if not already:
                    valid_student_ids.append(student_id)

        if not valid_student_ids:
            raise HTTPException(
                status_code=400,
                detail="No eligible students found. All may already be subscribed."
            )

        total_amount = org.subscription_price * len(valid_student_ids)
        reference = f"bulk_{uuid.uuid4().hex[:16]}"

        # Create bulk record
        bulk = BulkOrgSubscription(
            org_id=creator.org_id,
            exam_body_org_id=exam_body_org_id,
            created_by=creator.id,
            student_count=len(valid_student_ids),
            total_amount=total_amount,
            currency=org.subscription_currency,
            paystack_reference=reference,
            status=SubscriptionStatus.PENDING,
        )
        session.add(bulk)
        session.flush()

        # Create individual pending subscriptions
        for student_id in valid_student_ids:
            sub = ExamBodySubscription(
                student_id=student_id,
                exam_body_org_id=exam_body_org_id,
                org_id=creator.org_id,
                plan=SubscriptionPlan.PAID,
                status=SubscriptionStatus.PENDING,
                subscribed_by=SubscribedBy.ORGANIZATION,
                paystack_reference=reference,   # same ref — activated together on payment
                amount_paid=org.subscription_price,
                currency=org.subscription_currency,
            )
            session.add(sub)

        session.commit()

        paystack_response = await paystack.initialize_payment(
            email=creator.email,            # school pays, uses school email
            amount_kobo=int(total_amount * 100),
            reference=reference,
            callback_url=callback_url,
            metadata={
                "org_id": str(creator.org_id),
                "exam_body_org_id": str(exam_body_org_id),
                "bulk_subscription_id": str(bulk.id),
                "student_count": len(valid_student_ids),
            },
        )

        return {
            "payment_url": paystack_response["data"]["authorization_url"],
            "reference": reference,
            "student_count": len(valid_student_ids),
            "total_amount": total_amount,
            "currency": org.subscription_currency,
        }

    # --------------------------------------------------------
    # WEBHOOK — Paystack calls this on payment confirmation
    # --------------------------------------------------------

    @staticmethod
    async def handle_webhook(
        session: Session,
        event: str,
        data: dict,
    ) -> None:
        if event != "charge.success":
            return

        reference = data.get("reference")
        transaction_id = str(data.get("id", ""))

        if not reference:
            return

        # Determine if individual or bulk
        if reference.startswith("bulk_"):
            await ExamSubscriptionService._activate_bulk(
                session, reference, transaction_id
            )
        else:
            await ExamSubscriptionService._activate_individual(
                session, reference, transaction_id
            )

    @staticmethod
    async def _activate_individual(
        session: Session,
        reference: str,
        transaction_id: str,
    ) -> None:
        subscription = session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.paystack_reference == reference,
                ExamBodySubscription.status == SubscriptionStatus.PENDING,
            )
        ).first()

        if not subscription:
            return

        # Verify with Paystack before activating
        verification = await paystack.verify_payment(reference)
        if verification["data"]["status"] != "success":
            return

        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == subscription.exam_body_org_id
            )
        ).first()

        now = datetime.now(timezone.utc)
        subscription.status = SubscriptionStatus.ACTIVE
        subscription.paystack_transaction_id = transaction_id
        subscription.starts_at = now
        if org and org.subscription_duration_days:
            subscription.expires_at = now + timedelta(
                days=org.subscription_duration_days
            )
        session.add(subscription)
        session.commit()

    @staticmethod
    async def _activate_bulk(
        session: Session,
        reference: str,
        transaction_id: str,
    ) -> None:
        bulk = session.exec(
            select(BulkOrgSubscription).where(
                BulkOrgSubscription.paystack_reference == reference,
                BulkOrgSubscription.status == SubscriptionStatus.PENDING,
            )
        ).first()

        if not bulk:
            return

        verification = await paystack.verify_payment(reference)
        if verification["data"]["status"] != "success":
            return

        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == bulk.exam_body_org_id
            )
        ).first()

        now = datetime.now(timezone.utc)
        expires_at = None
        if org and org.subscription_duration_days:
            expires_at = now + timedelta(days=org.subscription_duration_days)

        # Activate all individual subscriptions under this bulk ref
        subscriptions = session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.paystack_reference == reference,
                ExamBodySubscription.status == SubscriptionStatus.PENDING,
            )
        ).all()

        for sub in subscriptions:
            sub.status = SubscriptionStatus.ACTIVE
            sub.paystack_transaction_id = transaction_id
            sub.starts_at = now
            sub.expires_at = expires_at
            session.add(sub)

        bulk.status = SubscriptionStatus.ACTIVE
        bulk.paystack_transaction_id = transaction_id
        session.add(bulk)
        session.commit()

    # --------------------------------------------------------
    # QUERIES
    # --------------------------------------------------------

    @staticmethod
    def get_my_subscriptions(
        session: Session,
        student_id: UUID,
    ) -> list[ExamBodySubscription]:
        return session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.student_id == student_id,
            )
        ).all()
    


    @staticmethod
    def start_exam_body_trial(
        session: Session,
        student: UserModel,
        exam_body_org_id: UUID,
    ) -> ExamBodySubscription | None:
        """
        Auto-called when student first accesses a paid exam body.
        Gives them free trial days if configured.
        Returns None if no trial available.
        """
        org = session.exec(
            select(OrganizationModel).where(
                OrganizationModel.id == exam_body_org_id
            )
        ).first()

        if not org or org.subscription_plan == SubscriptionPlan.FREE:
            return None

        if not org.exam_body_trial_days or org.exam_body_trial_days == 0:
            return None

        # Check no previous trial or subscription
        existing = session.exec(
            select(ExamBodySubscription).where(
                ExamBodySubscription.student_id == student.id,
                ExamBodySubscription.exam_body_org_id == exam_body_org_id,
            )
        ).first()

        if existing:
            return None  # already had trial or subscription

        now = datetime.now(timezone.utc)
        trial = ExamBodySubscription(
            student_id=student.id,
            exam_body_org_id=exam_body_org_id,
            plan=SubscriptionPlan.PAID,
            status=SubscriptionStatus.ACTIVE,
            subscribed_by=SubscribedBy.SELF,
            is_trial=True,
            trial_ends_at=now + timedelta(days=org.exam_body_trial_days),
            starts_at=now,
            expires_at=now + timedelta(days=org.exam_body_trial_days),
        )
        session.add(trial)
        session.commit()
        session.refresh(trial)
        return trial