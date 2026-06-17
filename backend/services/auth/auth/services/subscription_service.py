# auth/services/subscription_service.py
from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select, func, or_

from auth.database.schema.organization.organization_db import (
    OrganizationModel, OrganizationVisibility
)
from auth.database.schema.membership.membership_db import (
    OrgMembership, MembershipJoinType
)
from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.utility.password.password_harsher import PasswordHasher
from auth.api_models.student_signup import StudentSelfSignup


class SubscriptionService:

    # --------------------------------------------------------
    # STUDENT SELF SIGNUP
    # --------------------------------------------------------

    @staticmethod
    def student_self_signup(
        session: Session,
        payload: StudentSelfSignup,
    ) -> UserModel:
        """
        Creates a global student identity with no org attachment.
        Student then subscribes to orgs separately.
        """
        if payload.password != payload.confirm_password:
            raise HTTPException(status_code=422, detail="Passwords do not match.")

        existing = session.exec(
            select(UserModel).where(UserModel.email == payload.email.lower().strip())
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please log in."
            )

        user = UserModel(
            firstname=payload.firstname,
            lastname=payload.lastname,
            othername=payload.othername,
            email=payload.email.lower().strip(),
            phone=payload.phone,
            password=PasswordHasher.create(payload.password),
            verified=False,
            is_first_login=False,   # password-based, no Q&A setup needed
            role=UserRole.STUDENT,
            verification_method=VerificationMethod.EMAIL_OTP
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    # --------------------------------------------------------
    # ORGANIZATION DISCOVERY
    # --------------------------------------------------------

    @staticmethod
    def search_public_orgs(
        session: Session,
        query: str | None = None,
        category: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[OrganizationModel], int]:
        """
        Returns public organizations discoverable by any student.
        """
        stmt = select(OrganizationModel).where(
            OrganizationModel.visibility == OrganizationVisibility.PUBLIC,
            OrganizationModel.allow_self_subscription == True,
        )

        if query:
            stmt = stmt.where(
                or_(
                    OrganizationModel.name.ilike(f"%{query}%"),
                    OrganizationModel.slug.ilike(f"%{query}%"),
                    OrganizationModel.description.ilike(f"%{query}%"),
                )
            )

        if category:
            stmt = stmt.where(OrganizationModel.organization_type == category)

        total = session.exec(
            select(func.count()).select_from(stmt.subquery())
        ).one()

        orgs = session.exec(
            stmt.offset((page - 1) * per_page).limit(per_page)
        ).all()

        return orgs, total

    @staticmethod
    def get_my_organizations(
        session: Session,
        user_id: UUID,
    ) -> list[tuple[OrgMembership, OrganizationModel]]:
        """All orgs the student belongs to — invited + self-joined."""
        results = session.exec(
            select(OrgMembership, OrganizationModel)
            .join(OrganizationModel, OrganizationModel.id == OrgMembership.org_id)
            .where(
                OrgMembership.user_id == user_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).all()
        return results

    # --------------------------------------------------------
    # SELF SUBSCRIPTION
    # --------------------------------------------------------

    @staticmethod
    def subscribe_to_org(
        session: Session,
        user: UserModel,
        org_id: UUID,
    ) -> OrgMembership:
        """Student subscribes to a public exam body."""
        org = session.exec(
            select(OrganizationModel).where(OrganizationModel.id == org_id)
        ).first()

        if not org:
            raise HTTPException(status_code=404, detail="Organization not found.")

        if org.visibility != OrganizationVisibility.PUBLIC:
            raise HTTPException(
                status_code=403,
                detail="This organization does not accept public subscriptions."
            )

        if not org.allow_self_subscription:
            raise HTTPException(
                status_code=403,
                detail="This organization does not allow self-subscription."
            )

        # Check already a member
        existing = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"You are already subscribed to {org.name}."
            )

        membership = OrgMembership(
            user_id=user.id,
            org_id=org_id,
            role=UserRole.STUDENT,
            status=MembershipStatus.ACTIVE,
            join_type=MembershipJoinType.SELF_JOINED,
            verification_method=VerificationMethod.EMAIL_OTP,
            created_by=user.id,
        )
        session.add(membership)
        session.commit()
        session.refresh(membership)
        return membership

    @staticmethod
    def unsubscribe_from_org(
        session: Session,
        user: UserModel,
        org_id: UUID,
    ) -> None:
        """Student removes themselves from a public org they self-joined."""
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
                OrgMembership.join_type == MembershipJoinType.SELF_JOINED,
            )
        ).first()

        if not membership:
            raise HTTPException(
                status_code=404,
                detail="Subscription not found. You can only unsubscribe from organizations you joined yourself."
            )

        session.delete(membership)
        session.commit()

    # --------------------------------------------------------
    # ORG ADDS EXISTING USER (duplicate email flow)
    # --------------------------------------------------------

    @staticmethod
    def add_existing_user_to_org(
        session: Session,
        email: str,
        role: UserRole,
        org_id: UUID,
        creator: UserModel,
        institution_id: str | None = None,
    ) -> tuple[OrgMembership, UserModel, bool]:
        """
        Returns (membership, user, is_new_to_org).
        If user already exists globally, just add them to this org.
        If user doesn't exist, raise 404 — caller should use create_staff instead.
        """
        user = session.exec(
            select(UserModel).where(
                UserModel.email == email.lower().strip()
            )
        ).first()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="No account found with this email. Use the create staff/student endpoint to create a new account."
            )

        # Check already in this org
        existing = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"This user is already a member of your organization with role '{existing.role}'."
            )

        membership = OrgMembership(
            user_id=user.id,
            org_id=org_id,
            role=role,
            status=MembershipStatus.ACTIVE,
            join_type=MembershipJoinType.AUTO_ADDED,
            verification_method=VerificationMethod.EMAIL_OTP if user.verified else None,
            institution_id=institution_id,
            created_by=creator.id,
        )
        session.add(membership)
        session.commit()
        session.refresh(membership)
        return membership, user, True