"""
Schools inviting students by email. The school gives an email; Juwazi emails
a link. Someone new sets their name and password; someone who already has a
Juwazi account confirms with their password. Either way they then sign in with
email and password, and join the school as a student.

The invite is the student's pending membership (as when a school enrols a
student); the link carries its id and the email it was sent to.
"""
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from auth.database.schema.membership.enum import MembershipJoinType
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.organization.organization_db import OrganizationModel
from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.database.schema.user.user_db import UserModel
from auth.services.user import institution_ids
from auth.utility.jwt.token_activation import create_student_invite_token, verify_student_invite_token
from auth.utility.password.password_harsher import PasswordHasher

INVALID_LINK = "This invite link isn't valid or has expired. Ask your school to send a new one."


def normalise(email: str) -> str:
    return email.strip().lower()


@dataclass(frozen=True)
class Invited:
    membership: OrgMembership
    user: UserModel
    has_account: bool        # already had a Juwazi sign-in


def invite(session: Session, org_id: UUID, email: str, invited_by: UUID) -> Invited:
    """Make (or renew) the student's pending place in the school."""
    email = normalise(email)
    user = session.exec(select(UserModel).where(UserModel.email == email)).first()
    has_account = user is not None and user.password is not None
    if user is None:
        user = UserModel(
            firstname="", lastname="", email=email, verified=False, is_first_login=True,
            verification_method=VerificationMethod.EMAIL_OTP,
        )
        session.add(user)
        session.flush()

    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user.id, OrgMembership.org_id == org_id)
    ).first()
    if membership is not None:
        if membership.status == MembershipStatus.ACTIVE:
            raise HTTPException(status_code=409, detail=f"{email} is already in your organisation.")
        if membership.status == MembershipStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"{email} has already been invited. Send the invite again instead.")
        if membership.role != UserRole.STUDENT:
            raise HTTPException(status_code=409, detail=f"{email} was a staff member here; add them as staff instead.")
        membership.status = MembershipStatus.PENDING          # removed or archived before: invite again
        membership.join_type = MembershipJoinType.INVITED
    else:
        membership = OrgMembership(
            user_id=user.id, org_id=org_id, role=UserRole.STUDENT, status=MembershipStatus.PENDING,
            join_type=MembershipJoinType.INVITED, verification_method=VerificationMethod.EMAIL_OTP,
            created_by=invited_by,
        )
    if not membership.institution_id:
        membership.institution_id = institution_ids.generate(session)
        membership.institution_id_generated = True
    session.add(membership)
    session.commit()
    session.refresh(membership)
    session.refresh(user)
    return Invited(membership=membership, user=user, has_account=has_account)


def pending_invite(session: Session, org_id: UUID, user_id: UUID) -> Invited:
    """An invite to send again."""
    membership = session.exec(
        select(OrgMembership).where(OrgMembership.user_id == user_id, OrgMembership.org_id == org_id)
    ).first()
    user = session.get(UserModel, user_id)
    if (
        membership is None or user is None or not user.email or membership.role != UserRole.STUDENT
        or membership.status != MembershipStatus.PENDING or user.access_code is not None
    ):
        raise HTTPException(status_code=404, detail="No email invite waiting for this student.")
    return Invited(membership=membership, user=user, has_account=user.password is not None)


def link_token(invited: Invited) -> str:
    return create_student_invite_token(str(invited.membership.id), invited.user.email or "")


def needs_name(user: UserModel) -> bool:
    return not (user.firstname or "").strip() or not (user.lastname or "").strip()


@dataclass(frozen=True)
class OpenInvite:
    membership: OrgMembership
    user: UserModel
    org: OrganizationModel


def open_invite(session: Session, token: str) -> OpenInvite:
    """The invite a link is for, while it's still waiting."""
    try:
        membership_id, email = verify_student_invite_token(token)
        membership = session.get(OrgMembership, UUID(membership_id))
    except ValueError:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    if membership is None or membership.role != UserRole.STUDENT:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    user = session.get(UserModel, membership.user_id)
    org = session.get(OrganizationModel, membership.org_id)
    if user is None or org is None or normalise(user.email or "") != email:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    if membership.status == MembershipStatus.ACTIVE:
        raise HTTPException(status_code=409, detail=f"You've already joined {org.name}. Sign in with your email.")
    if membership.status != MembershipStatus.PENDING:
        raise HTTPException(status_code=400, detail=INVALID_LINK)
    return OpenInvite(membership=membership, user=user, org=org)


def accept(
    session: Session, token: str, *, password: str, firstname: str | None = None,
    lastname: str | None = None, confirm_password: str | None = None,
) -> OpenInvite:
    """Join the school. A new person sets their name and password; someone with
    an account proves it's them with their password."""
    found = open_invite(session, token)
    user = found.user
    if user.password is not None:
        if not PasswordHasher.verify(password, user.password):
            raise HTTPException(status_code=400, detail="That password isn't right for this account.")
    else:
        if needs_name(user):
            first, last = (firstname or "").strip(), (lastname or "").strip()
            if not first or not last:
                raise HTTPException(status_code=422, detail="Enter your first and last name.")
            user.firstname, user.lastname = first, last
        if len(password) < 8:
            raise HTTPException(status_code=422, detail="Use at least 8 characters for your password.")
        if password != confirm_password:
            raise HTTPException(status_code=422, detail="Passwords do not match.")
        user.password = PasswordHasher.create(password)
        if user.access_code is None:
            # Access-code students keep their own first-time setup at their other school.
            user.is_first_login = False
    user.verified = True                       # the link went to their email
    if user.verification_method is None:
        user.verification_method = VerificationMethod.EMAIL_OTP
    found.membership.status = MembershipStatus.ACTIVE
    found.membership.verification_method = VerificationMethod.EMAIL_OTP
    session.add(user)
    session.add(found.membership)
    session.commit()
    session.refresh(user)
    session.refresh(found.membership)
    return found
