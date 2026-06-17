# auth/services/membership_service.py
from uuid import UUID
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlmodel import Session, select

from auth.database.schema.user.user_db import UserModel
from auth.database.schema.user.enums import UserRole, MembershipStatus, VerificationMethod
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.database.schema.membership.enum import MembershipJoinType
from auth.database.database import SessionDep


class MembershipService:

    # --------------------------------------------------------
    # LOOKUPS
    # --------------------------------------------------------

    @staticmethod
    def get_active_membership(
        session: Session,
        user_id: UUID,
    ) -> OrgMembership | None:
        return session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

    @staticmethod
    def get_pending_membership(
        session: Session,
        user_id: UUID,
    ) -> OrgMembership | None:
        return session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.status == MembershipStatus.PENDING,
            )
        ).first()

    @staticmethod
    def get_pending_org_for_user(
        session: Session,
        user_id: UUID,
    ) -> OrganizationModel | None:
        """
        Find the org this user owns but hasn't verified into yet.
        Used during OTP verification — org is always available via owner_user_id
        regardless of OTP expiry.
        """
        return session.exec(
            select(OrganizationModel).where(
                OrganizationModel.owner_user_id == user_id
            )
        ).first()

    @staticmethod
    def get_org_members(
        session: Session,
        org_id: UUID,
        status: MembershipStatus = MembershipStatus.ACTIVE,
        role: UserRole | None = None,
    ) -> list[tuple[UserModel, OrgMembership]]:
        query = (
            select(UserModel, OrgMembership)
            .join(OrgMembership, OrgMembership.user_id == UserModel.id)
            .where(
                OrgMembership.org_id == org_id,
                OrgMembership.status == status,
            )
        )
        if role:
            query = query.where(OrgMembership.role == role)
        return session.exec(query).all()

    # --------------------------------------------------------
    # CREATION
    # --------------------------------------------------------

    @staticmethod
    def create_pending_membership(
        session: Session,
        user_id: UUID,
        org_id: UUID,
        role: UserRole,
        created_by: UUID,
        institution_id: str | None = None,
    ) -> OrgMembership:
        """
        Created when admin creates a staff or student member.
        Status is PENDING until they activate/setup their account.
        """
        # Guard against duplicate pending membership
        existing = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == org_id,
            )
        ).first()
        if existing:
            return existing

        membership = OrgMembership(
            user_id=user_id,
            org_id=org_id,
            role=role,
            status=MembershipStatus.PENDING,
            join_type=MembershipJoinType.INVITED,
            institution_id=institution_id,
            created_by=created_by,
        )
        session.add(membership)
        # Caller handles commit
        return membership

    @staticmethod
    def auto_add_on_verification(
        session: Session,
        user: UserModel,
        org_id: UUID,
        role: UserRole,
        created_by: UUID,
        verification_method: VerificationMethod,
        institution_id: str | None = None,
    ) -> OrgMembership:
        """
        Called on OTP verification (SUPER_ADMIN signup flow).
        Creates or reactivates membership.
        """
        existing = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.org_id == org_id,
            )
        ).first()

        if existing:
            if existing.status != MembershipStatus.ACTIVE:
                existing.status = MembershipStatus.ACTIVE
                existing.role = role
                existing.verification_method = verification_method
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
            return existing

        membership = OrgMembership(
            user_id=user.id,
            org_id=org_id,
            role=role,
            status=MembershipStatus.ACTIVE,
            join_type=MembershipJoinType.SELF_JOINED if role == UserRole.SUPER_ADMIN else MembershipJoinType.INVITED,
            verification_method=verification_method,
            institution_id=institution_id,
            created_by=created_by,
        )
        session.add(membership)
        return membership

    @staticmethod
    def activate_pending_membership(
        session: Session,
        user_id: UUID,
        verification_method: VerificationMethod,
    ) -> OrgMembership:
        """
        Activates the PENDING membership after staff activation or student setup.
        Returns the now-active membership.
        """
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.status == MembershipStatus.PENDING,
            )
        ).first()

        if not membership:
            raise HTTPException(
                status_code=400,
                detail="No pending membership found. "
                       "Please contact your administrator.",
            )

        membership.status = MembershipStatus.ACTIVE
        membership.verification_method = verification_method
        membership.updated_at = datetime.now(timezone.utc)
        session.add(membership)
        # Caller handles commit
        return membership

    # --------------------------------------------------------
    # UPDATES
    # --------------------------------------------------------

    @staticmethod
    def update_role(
        session: Session,
        user_id: UUID,
        org_id: UUID,
        new_role: UserRole,
        actor: UserModel,
    ) -> OrgMembership:
        """
        Orgs can update roles for non-student staff.
        Students manage their own profile after onboarding.
        """
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        if not membership:
            raise HTTPException(
                status_code=404,
                detail="Active membership not found."
            )

        if membership.role == UserRole.STUDENT:
            raise HTTPException(
                status_code=403,
                detail="Student roles cannot be changed by the organization.",
            )

        if new_role == UserRole.STUDENT:
            raise HTTPException(
                status_code=400,
                detail="Cannot assign student role via role update. "
                       "Create a student account instead.",
            )

        if membership.role == UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403,
                detail="Cannot change the organization owner's role.",
            )

        membership.role = new_role
        membership.updated_at = datetime.now(timezone.utc)
        session.add(membership)
        session.commit()
        session.refresh(membership)
        return membership

    # --------------------------------------------------------
    # REMOVAL
    # --------------------------------------------------------

    @staticmethod
    def archive_or_remove(
        session: Session,
        user_id: UUID,
        org_id: UUID,
        actor: UserModel,
        reason: str | None = None,
    ) -> dict:
        """
        Archive if user verified via email (preserve audit trail).
        Remove completely if access-code only (student with no email history).
        """
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user_id,
                OrgMembership.org_id == org_id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        if not membership:
            raise HTTPException(
                status_code=404,
                detail="Active membership not found.",
            )

        user = session.exec(
            select(UserModel).where(UserModel.id == user_id)
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        if user_id == actor.id:
            raise HTTPException(
                status_code=400,
                detail="You cannot remove yourself from the organization.",
            )

        if membership.role == UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=403,
                detail="Cannot remove the organization owner.",
            )

        if membership.verification_method == VerificationMethod.EMAIL_OTP:
            # Email-verified — archive, preserve history
            membership.status = MembershipStatus.ARCHIVED
            membership.role = UserRole.UNASSIGNED
            membership.archived_by = actor.id
            membership.archived_at = datetime.now(timezone.utc)
            membership.archive_reason = reason
            membership.updated_at = datetime.now(timezone.utc)
            session.add(membership)
            session.commit()
            return {"action": "archived", "user_id": str(user_id)}
        else:
            # Access-code only — full removal
            session.delete(membership)
            session.commit()
            return {"action": "removed", "user_id": str(user_id)}

    # @staticmethod
    # def auto_add_on_verification(
    #     session: Session,
    #     user: UserModel,
    #     org_id: UUID,
    #     role: UserRole,
    #     created_by: UUID,
    #     verification_method: VerificationMethod,
    #     institution_id: str | None = None,
    # ) -> OrgMembership:
    #     """
    #     Called automatically when a user completes verification.
    #     Idempotent — safe to call multiple times.
    #     """
    #     existing = session.exec(
    #         select(OrgMembership).where(
    #             OrgMembership.user_id == user.id,
    #             OrgMembership.org_id == org_id,
    #         )
    #     ).first()

    #     if existing:
    #         if existing.status != MembershipStatus.ACTIVE:
    #             existing.status = MembershipStatus.ACTIVE
    #             existing.role = role
    #             existing.updated_at = datetime.now(timezone.utc)
    #             session.add(existing)
    #             session.commit()
    #         return existing

    #     membership = OrgMembership(
    #         user_id=user.id,
    #         org_id=org_id,
    #         role=role,
    #         status=MembershipStatus.PENDING,
    #         institution_id=institution_id,
    #         verification_method=verification_method,
    #         created_by=created_by,
    #     )
    #     session.add(membership)
    #     return membership

    # @staticmethod
    # def archive_or_remove(
    #     session: Session,
    #     user_id: UUID,
    #     org_id: UUID,
    #     actor: UserModel,
    #     reason: str | None = None,
    # ) -> dict:
    #     membership = session.exec(
    #         select(OrgMembership).where(
    #             OrgMembership.user_id == user_id,
    #             OrgMembership.org_id == org_id,
    #             OrgMembership.status == MembershipStatus.ACTIVE,
    #         )
    #     ).first()

    #     if not membership:
    #         raise HTTPException(status_code=404, detail="Active membership not found.")

    #     user = session.exec(select(UserModel).where(UserModel.id == user_id)).first()
    #     if not user:
    #         raise HTTPException(status_code=404, detail="User not found.")

    #     if user_id == actor.id:
    #         raise HTTPException(status_code=400, detail="You cannot remove yourself.")

    #     if membership.role == UserRole.SUPER_ADMIN:
    #         raise HTTPException(status_code=403, detail="Cannot remove the organization owner.")

    #     if membership.verification_method == VerificationMethod.EMAIL_OTP:
    #         # Has email — archive, preserve record
    #         membership.status = MembershipStatus.ARCHIVED
    #         # membership.role = UserRole.UNASSIGNED
    #         membership.archived_by = actor.id
    #         membership.archived_at = datetime.now(timezone.utc)
    #         membership.archive_reason = reason
    #         membership.updated_at = datetime.now(timezone.utc)
    #         session.add(membership)
    #         session.commit()
    #         return {"action": "archived", "user_id": str(user_id)}
    #     else:
    #         # Access code only — remove completely
    #         membership.status = MembershipStatus.REMOVED
    #         session.delete(membership)
    #         session.commit()
    #         return {"action": "removed", "user_id": str(user_id)}

    # @staticmethod
    # def update_role(
    #     session: Session,
    #     user_id: UUID,
    #     org_id: UUID,
    #     new_role: UserRole,
    #     actor: UserModel,
    # ) -> OrgMembership:
    #     membership = session.exec(
    #         select(OrgMembership).where(
    #             OrgMembership.user_id == user_id,
    #             OrgMembership.org_id == org_id,
    #             OrgMembership.status == MembershipStatus.ACTIVE,
    #         )
    #     ).first()

    #     if not membership:
    #         raise HTTPException(status_code=404, detail="Active membership not found.")

    #     if membership.role == UserRole.STUDENT:
    #         raise HTTPException(
    #             status_code=403,
    #             detail="Student roles cannot be changed by the organization."
    #         )

    #     if new_role == UserRole.STUDENT:
    #         raise HTTPException(
    #             status_code=400,
    #             detail="Cannot assign student role via role update."
    #         )

    #     membership.role = new_role
    #     membership.updated_at = datetime.now(timezone.utc)
    #     session.add(membership)
    #     session.commit()
    #     session.refresh(membership)
    #     return membership
    
    # @staticmethod
    # def get_pending_org_for_user(
    #     session: Session,
    #     user_id: UUID,
    # ) -> OrganizationModel | None:
    #     """
    #     Find the org this user owns but hasn't verified into yet.
    #     Used by request_otp and verify_otp to locate the org
    #     without needing any field on UserModel.
    #     """
    #     return session.exec(
    #         select(OrganizationModel).where(
    #             OrganizationModel.owner_user_id == user_id
    #         )
    #     ).first()
    

    # @staticmethod
    # def create_pending_membership(
    #     session: Session,
    #     user_id: UUID,
    #     org_id: UUID,
    #     role: UserRole,
    #     created_by: UUID,
    #     institution_id: str | None = None,
    # ) -> OrgMembership:
    #     """
    #     Created when admin creates a staff member.
    #     Status is 'pending' until they activate their account.
    #     """
    #     membership = OrgMembership(
    #         user_id=user_id,
    #         org_id=org_id,
    #         role=role,
    #         status=MembershipStatus.PENDING,               # not active until account activated
    #         join_type=MembershipJoinType.INVITED,
    #         verification_method=VerificationMethod.EMAIL_OTP,
    #         created_by=created_by,
    #         institution_id=institution_id
    #     )
    #     session.add(membership)
    #     # session.commit()
    #     # session.refresh(membership)
    #     return membership
    
    
    # @staticmethod
    # def get_active_membership(session: SessionDep, user_id: UUID) -> OrgMembership:
    #     """
    #     Fetch the user's active OrgMembership.
    #     Raises 403 if not found — used after activation/setup to issue tokens.
    #     """
    #     membership = session.exec(
    #         select(OrgMembership).where(
    #             OrgMembership.user_id == user_id,
    #             OrgMembership.status == MembershipStatus.ACTIVE,
    #         )
    #     ).first()

    #     if not membership:
    #         raise HTTPException(
    #             status_code=403,
    #             detail="No active organization membership found.",
    #         )
    #     return membership