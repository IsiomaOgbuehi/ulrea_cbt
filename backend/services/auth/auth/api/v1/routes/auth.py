import os
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import select
from auth.api_models.login_response import LoginResponse, TokenData
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import SessionDep
from auth.dependencies.user_dependencies import authenticate_user
from auth.utility.jwt.jwt import create_access_token, create_refresh_token, create_student_provisional_token, decode_refresh_token
from datetime import datetime, timezone
from auth.api_models import SignUp, SignUpResponse
from auth.database.schema import OrganizationModel, UserModel, OrganizationRead
from auth.api_models.user_api_models import StaffUserResponse, UserRead, UserReadResponse
from auth.utility.password.password_harsher import PasswordHasher
import jwt
from auth.utility.redis.redis_client import redis_client
from auth.api_models.schemas.otp import OTPResponse, OTPRequestSchema, OTPVerifyResponse, OTPVerifySchema
from auth.utility.otp.otp_service import OtpService
import logging
import asyncio
from auth.utility.email.email_service import EmailService
from auth.core.settings import settings
from auth.api_models.token import RefreshResponse, RefreshRequest
from uuid import UUID
from sqlalchemy.exc import IntegrityError

from auth.database.schema.user.enums import MembershipStatus, UserRole, VerificationMethod
from auth.utility.otp.otp_enums import OtpPurpose
from auth.services.membership_service import MembershipService
from auth.database.schema.platform_subscription.platform_subscription_db import PlatformPlan
from auth.services.platform_subscription_service import PlatformSubscriptionService
from auth.database.schema.platform_subscription.enum import PlatformPlanStatus
from auth.database.schema.membership.membership_db import OrgMembership


IS_DEV = settings.ENVIRONMENT == "dev"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.API_VERSION.value}{AuthRoutes.BASE_ROUTE.value}{AuthRoutes.LOGIN.value}')

router = APIRouter(
    prefix=AuthRoutes.BASE_ROUTE.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)

async def handle_unverified_user(user: UserModel):
    try:
        otp = await OtpService.request_otp(
            purpose=OtpPurpose.LOGIN_VERIFICATION,
            identifier=user.email,
        )

        await EmailService.send_otp_email(user.email, otp)

        return {
            "message": "Account not verified. OTP has been sent.",
            "code": "ACCOUNT_NOT_VERIFIED",
            "action": "VERIFY_OTP",
            "identifier": user.email
        }
    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))  # 429 = Too Many Requests
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 


'''LOGIN 🔐 '''
@router.post(AuthRoutes.LOGIN.value, response_model=LoginResponse)
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep):

    user = authenticate_user(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # User must verify email before login
    if not user.verified:
        # Check if this is a staff member created by an admin (has pending membership)
        # vs a SUPER_ADMIN who signed up themselves (needs OTP)

        pending_membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.PENDING,
            )
        ).first()

        is_admin_created_staff = (
            pending_membership is not None
            and pending_membership.role != UserRole.STUDENT
        )

        if is_admin_created_staff:
            # Activation link expired or never clicked — tell frontend to resend
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Account not activated.",
                    "code": "ACTIVATION_REQUIRED",
                    "action": "RESEND_ACTIVATION",
                    "identifier": user.email,
                }
            )

        detail = await handle_unverified_user(user)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )

    membership = session.exec(
        select(OrgMembership)
        .where(
            OrgMembership.user_id == user.id,
            OrgMembership.status == MembershipStatus.ACTIVE,
        )
    ).first()

        # Staff on first login: not yet in OrgMembership — find their org via owner link
    # (for SUPER_ADMIN) or via the org that created them (for staff)
    if not membership:
        # Check if this is a self-signup student (password-based, no pending membership)
        pending = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.PENDING,
            )
        ).first()

        is_org_less_student = pending is None and user.verified

        if is_org_less_student:
            provisional = create_student_provisional_token(user.id)
            return LoginResponse(
                access_token=provisional.access_token,
                refresh_token="",
                user=StaffUserResponse.model_validate(user, from_attributes=True),
                organization=None,
                requires_setup=False,
                is_provisional=True,   # frontend redirects to org discovery
            )
    
        if user.is_first_login:
            # Try to find org via owner_user_id (SUPER_ADMIN who never verified)
            # or via the org that created this staff member
            owned_org = session.exec(
                select(OrganizationModel).where(
                    OrganizationModel.owner_user_id == user.id
                )
            ).first()

            if owned_org:
                pass
            else:
                # Staff created by admin — org is on their creator's membership
                # The org_id was set when admin created them — find it via
                # the organization that has this staff member's email in their domain
                # or fall back to requiring activation first
                raise HTTPException(
                    status_code=403,
                    detail="Please complete account activation before logging in."
                )
        else:
            raise HTTPException(
                status_code=403,
                detail="No active organization membership found."
            )
    
    owner_org = session.exec(
                select(OrganizationModel).where(
                    OrganizationModel.id == membership.org_id
                )
            ).first()


    token_data = create_access_token(user.id, membership.org_id, membership.role)
    refresh = create_refresh_token(user.id)

    return LoginResponse(
        access_token=token_data.access_token,
        refresh_token=refresh,
        user=StaffUserResponse.model_validate(user, from_attributes=True),
        organization=OrganizationRead.model_validate(owner_org, from_attributes=True) if owner_org else None,
        requires_setup=user.is_first_login,
    )

''' REFRESH TOKEN 🔄 '''
@router.post(AuthRoutes.REFRESH_TOKEN.value, response_model=RefreshResponse)
async def refresh_token(payload: RefreshRequest, session: SessionDep):

    try:
        token_payload = decode_refresh_token(payload.refresh_token)

    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token has expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token.")

    jti = token_payload.get('jti')
    is_blacklisted = await redis_client.get(f"blacklist:jti:{jti}")
    if is_blacklisted:
        raise HTTPException(status_code=401, detail="Refresh token has been revoked. Please log in again.")

    # ✅ Convert string back to UUID before querying
    try:
        user_id = UUID(token_payload.get('sub'))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=401, detail="Invalid token subject.")

    user = session.exec(
        select(UserModel).where(UserModel.id == user_id)
    ).first()

    membership = MembershipService.get_active_membership(session=session, user_id=user.id)

    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists.")

    if not user.verified:
        raise HTTPException(status_code=403, detail="Account is not verified.")

    new_token = create_access_token(user.id, membership.org_id, membership.role)

    return RefreshResponse(access_token=new_token.access_token)



''' LOGOUT USER 🔒 '''
@router.post(AuthRoutes.LOGOUT.value)
async def logout(payload: RefreshRequest, token: str = Depends(oauth2_scheme)):
    # Blacklist the access token
    access_payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    access_jti = access_payload['jti']
    access_exp = access_payload['exp']
    now = datetime.now(timezone.utc).timestamp()
    access_ttl = int(access_exp - now)
    if access_ttl > 0:
        await redis_client.set(f"blacklist:jti:{access_jti}", 1, ex=access_ttl)

    # Blacklist the refresh token too
    try:
        refresh_payload = decode_refresh_token(payload.refresh_token)
        refresh_jti = refresh_payload['jti']
        refresh_exp = refresh_payload['exp']
        refresh_ttl = int(refresh_exp - now)
        if refresh_ttl > 0:
            await redis_client.set(f"blacklist:jti:{refresh_jti}", 1, ex=refresh_ttl)
    except Exception:
        pass  # if refresh token is invalid/expired, no need to blacklist

    return {"detail": "Successfully logged out"}




''' SIGN UP 🧑‍💻 '''
@router.post(AuthRoutes.SIGNUP.value, response_model=SignUpResponse)
async def signup(signup_data: SignUp, session: SessionDep):

    user_email = signup_data.user.email.strip().lower()
    org_email = signup_data.organization.email.strip().lower()

    if signup_data.user.password != signup_data.user.confirm_password:
        raise HTTPException(
            status_code=422,
            detail={"message": "Password and Confirm password mismatch"}
        )

    existing_user = session.exec(
        select(UserModel).where(UserModel.email == user_email)
    ).first()
    if existing_user:
        raise HTTPException(
            status_code=409,
            detail={"message": "User email already exists. Please login."}
        )

    existing_org = session.exec(
        select(OrganizationModel).where(OrganizationModel.email == org_email)
    ).first()
    if existing_org:
        raise HTTPException(
            status_code=409,
            detail={"message": "Organization email already exists. Please login."}
        )

    try:
        # 1. Create user FIRST
        user = UserModel.model_validate(
            signup_data.user,
            update={
                "password": PasswordHasher.create(signup_data.user.password),
                "verified": False,
                "verification_method": VerificationMethod.EMAIL_OTP
            },
        )
        session.add(user)
        session.flush()  # user.id now available

        # 2. Create organization WITH owner_user_id already set
        organization = OrganizationModel.model_validate(
            signup_data.organization,
            update={
                "owner_user_id": user.id,
            },
        )
        session.add(organization)

        session.commit()

    except IntegrityError as e:
        session.rollback()
        raise HTTPException(
        status_code=409, 
        detail={"message": "Error", "error": str(e.orig)}
    )

    session.refresh(user)
    session.refresh(organization)

    # Generate OTP
    try:
        otp = await OtpService.request_otp(
            purpose=OtpPurpose.SIGNUP,
            identifier=user.email,
        )
    except Exception:
        logging.exception("OTP generation failed after signup commit")
        raise HTTPException(status_code=500, detail="Failed to generate OTP")

    # Send OTP
    try:
        await EmailService.send_otp_email(user.email, otp)
    except Exception:
        logging.exception("OTP email failed for %s", user.email)
        raise HTTPException(status_code=502, detail="Failed to send OTP email")

    if IS_DEV:
        return {
            "organization": OrganizationRead.model_validate(organization, from_attributes=True),
            "user": UserReadResponse.model_validate(user, from_attributes=True),
            "otp_sent_to": EmailService.mask_email(user.email),
            "otp": otp,
        }

    return SignUpResponse(
        organization=OrganizationRead.model_validate(organization, from_attributes=True),
        user=UserReadResponse.model_validate(user, from_attributes=True),
        otp_sent_to=EmailService.mask_email(user.email),
    )




''' REQUEST OTP 📨 '''
@router.post(AuthRoutes.REQUEST_OTP.value, response_model=OTPResponse)
async def request_otp(payload: OTPRequestSchema, session: SessionDep):

    # For signup purpose, ensure the user actually exists before sending OTP
    if payload.purpose == OtpPurpose.SIGNUP:
        user = session.exec(
            select(UserModel).where(UserModel.email == payload.identifier)
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="No account found with this email.")

        if user.verified:
            raise HTTPException(status_code=400, detail="Account is already verified.")
        
        # Verify there's an org linked to this user
        # Works even after OTP expiry — stored permanently on OrganizationModel
        pending_org = MembershipService.get_pending_org_for_user(
            session, user.id
        )
        if not pending_org:
            raise HTTPException(
                status_code=400,
                detail="No organization found for this account. "
                       "Please sign up again."
            )

    try:
        otp = await OtpService.request_otp(
            purpose=payload.purpose,
            identifier=payload.identifier,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logging.exception("OTP generation error")
        raise HTTPException(status_code=500, detail="Internal error")

    try:
        await asyncio.wait_for(
            EmailService.send_otp_email(payload.identifier, otp),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        logging.error("OTP email timed out for %s", payload.identifier)
        await OtpService.invalidate_otp(payload.purpose, payload.identifier)
        raise HTTPException(status_code=502, detail="Email service timed out. Please try again.")
    except Exception:
        logging.exception("OTP email failed for %s", payload.identifier)
        await OtpService.invalidate_otp(payload.purpose, payload.identifier)
        raise HTTPException(status_code=502, detail="Failed to send OTP email. Please try again.")

    if IS_DEV:
        return OTPResponse(message="OTP sent successfully", otp=otp)

    return OTPResponse(message="OTP sent successfully")





''' VERIFY OTP ✅ '''
@router.post(AuthRoutes.VERIFY_OTP.value, response_model=OTPVerifyResponse)
async def verify_otp(payload: OTPVerifySchema, session: SessionDep):
    try:
        is_valid = await OtpService.verify_otp(
            purpose=payload.purpose,
            identifier=payload.identifier,
            otp=payload.otp,
        )

        if not is_valid:
            raise HTTPException(status_code=400, detail="Invalid OTP.")

        user = session.exec(
            select(UserModel).where(UserModel.email == payload.identifier)
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="User not found.")

        if not user.verified:
            user.verified = True
            session.add(user)
            session.flush()

            if payload.purpose == OtpPurpose.SIGNUP:
                org = MembershipService.get_pending_org_for_user(session, user.id)

                is_self_signup_student = org is None

                if is_self_signup_student:
                    # Self-signup student — no org yet, just mark verified.
                    # They subscribe to orgs via GET /student/organizations.
                    session.commit()
                    session.refresh(user)

                    provisional = create_student_provisional_token(user.id)

                    # No token yet — they have no org_id to encode.
                    # Return verified=True and let frontend redirect to org discovery.
                    return OTPVerifyResponse(
                        message="Email verified. Please subscribe to an organization to continue.",
                        verified=True,
                        token=TokenData(
                            access_token=provisional.access_token,
                            refresh_token="",   # no refresh — provisional only
                        ),
                        is_provisional=True,    # signal to frontend to go to org discovery
                    )

                # SUPER_ADMIN signup — has an org
                MembershipService.auto_add_on_verification(
                    session=session,
                    user=user,
                    org_id=org.id,
                    role=UserRole.SUPER_ADMIN,
                    created_by=user.id,
                    verification_method=VerificationMethod.EMAIL_OTP,
                )

                try:
                    default_plan = session.exec(
                        select(PlatformPlan).where(
                            PlatformPlan.trial_days > 0,
                            PlatformPlan.status == PlatformPlanStatus.ACTIVE,
                        ).order_by(PlatformPlan.price)
                    ).first()

                    if default_plan:
                        PlatformSubscriptionService.start_trial(
                            session=session,
                            org_id=org.id,
                            plan_id=default_plan.id,
                        )
                except Exception:
                    logging.warning("Could not auto-start trial for org %s", org.id)

                session.commit()
                session.refresh(user)

        # Issue token — requires active membership
        membership = session.exec(
            select(OrgMembership).where(
                OrgMembership.user_id == user.id,
                OrgMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()

        if not membership:
            # Verified student with no org yet — still no token
            return OTPVerifyResponse(
                message="Email verified. Please subscribe to an organization to continue.",
                verified=True,
                token=None,
            )

        access_token = create_access_token(user.id, membership.org_id, membership.role)
        refresh_token = create_refresh_token(user.id)

        return OTPVerifyResponse(
            message="OTP verified successfully.",
            verified=True,
            token=TokenData(
                access_token=access_token.access_token,
                refresh_token=refresh_token,
            )
        )

    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logging.exception("OTP verification error")
        raise HTTPException(status_code=500, detail="Internal server error")




# ''' VERIFY OTP ✅ '''
# @router.post(AuthRoutes.VERIFY_OTP.value, response_model=OTPVerifyResponse)
# async def verify_otp(payload: OTPVerifySchema, session: SessionDep):

#     try:
#         is_valid = await OtpService.verify_otp(
#             purpose=payload.purpose,
#             identifier=payload.identifier,
#             otp=payload.otp,
#         )

#         if not is_valid:
#             raise HTTPException(status_code=400, detail="Invalid OTP.")

#         user = session.exec(
#             select(UserModel).where(UserModel.email == payload.identifier)
#         ).first()

#         if not user:
#             raise HTTPException(status_code=404, detail="User not found.")

#         if not user.verified:
#             user.verified = True
#             session.add(user)
#             session.flush()

#             if payload.purpose == OtpPurpose.SIGNUP:
#                 # Find org via owner_user_id — always available, no expiry
#                 org = MembershipService.get_pending_org_for_user(session, user.id)

#                 if not org:
#                     raise HTTPException(
#                         status_code=400,
#                         detail="No organization found. Please sign up again."
#                     )

#                 # Register membership
#                 MembershipService.auto_add_on_verification(
#                     session=session,
#                     user=user,
#                     org_id=org.id,
#                     role=UserRole.SUPER_ADMIN,
#                     created_by=user.id,
#                     verification_method=VerificationMethod.EMAIL_OTP,
#                 )

#                 # Auto-start platform trial
#                 try:
#                     default_plan = session.exec(
#                         select(PlatformPlan).where(
#                             PlatformPlan.trial_days > 0,
#                             PlatformPlan.status == PlatformPlanStatus.ACTIVE,
#                         ).order_by(PlatformPlan.price)
#                     ).first()

#                     if default_plan:
#                         PlatformSubscriptionService.start_trial(
#                             session=session,
#                             org_id=org.id,
#                             plan_id=default_plan.id,
#                         )
#                 except Exception:
#                     logging.warning(
#                         "Could not auto-start trial for org %s", org.id
#                     )

#                 session.commit()
#                 session.refresh(user)

#         # Determine org_id for token — get from active membership
#         membership = session.exec(
#             select(OrgMembership).where(
#                 OrgMembership.user_id == user.id,
#                 OrgMembership.status == MembershipStatus.ACTIVE,
#             )
#         ).first()

#         if not membership:
#             raise HTTPException(
#                 status_code=400,
#                 detail="No active organization membership found."
#             )

#         access_token = create_access_token(
#             user.id,
#             membership.org_id,
#             membership.role,
#         )
#         refresh_token = create_refresh_token(user.id)

#         return OTPVerifyResponse(
#             message="OTP verified successfully.",
#             verified=True,
#             token=TokenData(
#                 access_token=access_token.access_token,
#                 refresh_token=refresh_token,
#             )
#         )

#     except ValueError as e:
#         raise HTTPException(status_code=429, detail=str(e))
#     except HTTPException:
#         raise
#     except Exception:
#         logging.exception("OTP verification error")
#         raise HTTPException(status_code=500, detail="Internal server error")



'''
Issue JWT immediately after OTP verification (passwordless auth 🔥)
Add resend OTP cooldown
Implement OTP for login (passwordless login)
Mock Redis for tests (important for CI)
'''