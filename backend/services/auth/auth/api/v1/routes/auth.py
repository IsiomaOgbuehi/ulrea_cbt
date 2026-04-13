import os
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import select
from auth.api_models.login_response import LoginResponse, Token, User, TokenData
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import SessionDep
from auth.dependencies.user_dependencies import authenticate_user, get_current_active_user
from auth.utility.jwt.jwt import create_access_token, create_refresh_token, decode_refresh_token
from datetime import datetime, timezone
from auth.api_models import SignUp, SignUpResponse
from auth.database.schema import OrganizationModel, UserModel, OrganizationRead, UserRead
from auth.utility.password.password_harsher import PasswordHasher
import jwt
from auth.utility.redis.redis_client import redis_client
from auth.api_models.schemas.otp import OTPResponse, OTPRequestSchema, OTPVerifyResponse, OTPVerifySchema
from auth.utility.otp.otp_service import OtpService
import logging
from fastapi import BackgroundTasks
import asyncio
from auth.utility.email.email_service import EmailService
from auth.core.settings import settings
from auth.api_models.token import RefreshResponse, RefreshRequest
from uuid import UUID


IS_DEV = settings.ENVIRONMENT == "dev"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.API_VERSION.value}{AuthRoutes.BASE_ROUTE.value}{AuthRoutes.LOGIN.value}')

router = APIRouter(
    prefix=AuthRoutes.BASE_ROUTE.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)


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

    if not user.verified:
        raise HTTPException(status_code=403, detail="Account not verified. Please verify your email first.")

    token_data = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    organization = None
    if user.org_id:
        organization = session.exec(
            select(OrganizationModel).where(OrganizationModel.id == user.org_id)
        ).first()

    return LoginResponse(
        access_token=token_data.access_token,
        refresh_token=refresh_token,
        user=UserRead.model_validate(user, from_attributes=True),
        organization=OrganizationRead.model_validate(organization, from_attributes=True) if organization else None,
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

    if not user:
        raise HTTPException(status_code=401, detail="User no longer exists.")

    if not user.verified:
        raise HTTPException(status_code=403, detail="Account is not verified.")

    new_token = create_access_token(user.id)

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


''' GET TOKEN 🔑 '''
@router.get(AuthRoutes.TOKEN.value)
async def get_token(token: Annotated[str, Depends(oauth2_scheme)]):
    return {'token': token}


''' SIGN UP 🧑‍💻 '''
@router.post(AuthRoutes.SIGNUP.value, response_model=SignUpResponse)
async def signup(signup_data: SignUp, session: SessionDep):

    if signup_data.user.password != signup_data.user.confirm_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={'message': 'Password and Confirm password mismatch'}
        )

    # 1️⃣ Create organization and user inside a transaction
    organization = OrganizationModel.model_validate(signup_data.organization)
    session.add(organization)
    session.flush()  # get organization.id without committing yet

    user = UserModel.model_validate(
        signup_data.user,
        update={
            'org_id': organization.id,
            'password': PasswordHasher.create(signup_data.user.password),
            'verified': False,
        },
    )
    session.add(user)
    session.flush()  # get user.id without committing yet

    # 2️⃣ Generate OTP before committing — if this fails, nothing is saved
    try:
        otp = await OtpService.request_otp(
            purpose="signup",
            identifier=user.email,
        )
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        session.rollback()
        logging.exception("OTP generation error during signup")
        raise HTTPException(status_code=500, detail="Failed to generate OTP. Please try again.")

    # 3️⃣ Send OTP before committing — if this fails, nothing is saved
    try:
        await asyncio.wait_for(
            EmailService.send_otp_email(user.email, otp),
            timeout=10.0
        )
    except asyncio.TimeoutError:
        session.rollback()
        await OtpService.invalidate_otp("signup", user.email)
        logging.error("OTP email timed out during signup for %s", user.email)
        raise HTTPException(status_code=502, detail="Email service timed out. Please try again.")
    except Exception:
        session.rollback()
        await OtpService.invalidate_otp("signup", user.email)
        logging.exception("OTP email failed during signup for %s", user.email)
        raise HTTPException(status_code=502, detail="Failed to send OTP email. Please try again.")

    # 4️⃣ Everything succeeded — now commit
    session.commit()
    session.refresh(organization)
    session.refresh(user)

    response = SignUpResponse(
        organization=OrganizationRead.model_validate(organization, from_attributes=True),
        user=UserRead.model_validate(user, from_attributes=True),
        otp_sent_to=EmailService.mask_email(user.email)
    )

    return SignUpResponse(
        organization=OrganizationRead.model_validate(organization, from_attributes=True),
        user=UserRead.model_validate(user, from_attributes=True),
        otp_sent_to=EmailService.mask_email(user.email),
        otp=otp if IS_DEV else None,
    )

# @router.post(AuthRoutes.SIGNUP.value, response_model=SignUpResponse)
# async def signup(signup_data: SignUp, session: SessionDep):

#     if signup_data.user.password != signup_data.user.confirm_password:
#         raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={'message': 'Password and Confirm password mismatch'})

#     # 1️⃣ Create user (super admin)
#     organization = OrganizationModel.model_validate(signup_data.organization)

#     session.add(organization)
#     session.commit()
#     session.refresh(organization)

#     # 2️⃣ Create user (super admin)
#     user = UserModel.model_validate(
#         signup_data.user,
#         update={
#             'org_id': organization.id,
#             'password': PasswordHasher.create(signup_data.user.password)
#         },
#     )
#     session.add(user)
#     session.commit()
#     session.refresh(user)

#     token_data = create_access_token(user.id)
#     token = TokenData(access_token=token_data.access_token)

#     return SignUpResponse(
#         organization=OrganizationRead.model_validate(
#             organization,
#             from_attributes=True
#         ),
#         user=UserRead.model_validate(
#             user,
#             from_attributes=True
#         ),
#         token=token)


''' REQUEST OTP 📨 '''
@router.post(AuthRoutes.REQUEST_OTP.value, response_model=OTPResponse)
async def request_otp(payload: OTPRequestSchema, session: SessionDep):

    # For signup purpose, ensure the user actually exists before sending OTP
    if payload.purpose == "signup":
        user = session.exec(
            select(UserModel).where(UserModel.email == payload.identifier)
        ).first()

        if not user:
            raise HTTPException(status_code=404, detail="No account found with this email.")

        if user.verified:
            raise HTTPException(status_code=400, detail="Account is already verified.")

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


# @router.post(AuthRoutes.REQUEST_OTP.value, response_model=OTPResponse)
# async def request_otp(payload: OTPRequestSchema, background_tasks: BackgroundTasks):

    try:
        otp = await OtpService.request_otp(
            purpose=payload.purpose,
            identifier=payload.identifier
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logging.exception("OTP generation error")
        raise HTTPException(status_code=500, detail="Internal error")

    # Await directly — no background task
    try:
        await asyncio.wait_for(
        EmailService.send_otp_email(payload.identifier, otp),
        timeout=10.0  # seconds
    )
    except asyncio.TimeoutError:
        logging.error("OTP email timed out for %s", payload.identifier)
        await OtpService.invalidate_otp(payload.purpose, payload.identifier)
        raise HTTPException(status_code=502, detail="Email service timed out. Please try again.")
    except Exception:
        logging.exception("OTP email failed for %s", payload.identifier)
        # OTP was saved in Redis — roll it back so the user can retry cleanly
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

        if payload.purpose == "signup":
            user = session.exec(
                select(UserModel).where(UserModel.email == payload.identifier)
            ).first()

            if not user:
                raise HTTPException(status_code=404, detail="User not found.")

            if user.verified:
                raise HTTPException(status_code=400, detail="Account already verified.")

            # ✅ Activate account
            user.verified = True
            session.add(user)
            session.commit()
            session.refresh(user)

            # 🎟️ Issue token now that account is verified
            token_data = create_access_token(user.id)

            return OTPVerifyResponse(
                message="Account verified successfully.",
                verified=True,
                token=TokenData(access_token=token_data.access_token)
            )

    except ValueError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logging.exception("OTP verification error")
        raise HTTPException(status_code=500, detail="Internal server error")

    return OTPVerifyResponse(message="OTP verified successfully.", verified=True)



@router.get("/users/me/", response_model=User)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user


'''
Issue JWT immediately after OTP verification (passwordless auth 🔥)
Add resend OTP cooldown
Implement OTP for login (passwordless login)
Mock Redis for tests (important for CI)
'''

# @router.post("/heroes/", response_model=HeroModel)
# async def create_hero(hero: Hero, session: SessionDep):
    
#     db_hero = HeroModel.model_validate(hero)
#     session.add(db_hero)
#     session.commit()
#     session.refresh(db_hero)
#     return db_hero

# @router.put("/heroes/{hero_id}", response_model=HeroRead)
# async def update_hero_put(
#     hero_id: int,
#     hero: Hero,
#     session: SessionDep,
# ):
#     db_hero = session.get(HeroModel, hero_id)
#     if not db_hero:
#         raise HTTPException(status.HTTP_404_NOT_FOUND, detail={'message': 'Hero not Found'})

#     for key, value in hero.model_dump().items():
#         setattr(db_hero, key, value)

#     session.commit()
#     session.refresh(db_hero)
#     return db_hero

# @router.patch("/heroes/{hero_id}", response_model=HeroRead)
# def update_hero_patch(
#     hero_id: int,
#     hero: Hero,
#     session: SessionDep,
# ):
#     db_hero = session.get(HeroModel, hero_id)
#     if not db_hero:
#         raise HTTPException(404)

#     hero_data = hero.model_dump(exclude_unset=True)
#     for key, value in hero_data.items():
#         setattr(db_hero, key, value)

#     session.commit()
#     session.refresh(db_hero)
#     return db_hero