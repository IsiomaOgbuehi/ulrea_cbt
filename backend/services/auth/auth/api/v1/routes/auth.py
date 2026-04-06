import os
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import select
from auth.api_models.login_response import LoginResponse, Token, User, TokenData
from auth.api.v1.auth_routes import AuthRoutes
from auth.database.database import SessionDep
from auth.dependencies.user_dependencies import authenticate_user, get_current_active_user
from auth.utility.jwt.jwt import create_access_token
from datetime import datetime, timezone
from auth.api_models import SignUp, SignUpResponse
from auth.database.schema import OrganizationModel, UserModel, OrganizationRead, UserRead
from auth.utility.password.password_harsher import PasswordHasher
import jwt
from auth.utility.jwt.jwt import JWT_ALGORITHM, JWT_SECRET
from auth.utility.redis.redis_client import redis_client
from auth.api_models.schemas.otp import OTPResponse, OTPRequestSchema, OTPVerifyResponse, OTPVerifySchema
from auth.utility.otp.otp_service import OtpService
import traceback
import logging

IS_DEV = os.getenv("ENVIRONMENT") == 'dev'

# otp_secret = os.getenv("OTP_SECRET")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.API_VERSION.value}{AuthRoutes.BASE_ROUTE.value}{AuthRoutes.LOGIN.value}')

router = APIRouter(
    prefix=AuthRoutes.BASE_ROUTE.value,
    tags=['auth'],
    responses={401: {'message': 'Unauthorized'}}
)

'''LOGIN 🔐 '''
@router.post(AuthRoutes.LOGIN.value, response_model=TokenData, summary="Login with email and password",
    description="Use your **email address** as the username field.")
async def login(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], session: SessionDep):
    
    user = authenticate_user(session, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token_data = create_access_token(user.id)

    return TokenData(access_token=token_data.access_token)


''' LOGOUT USER 🔒 '''
@router.post(AuthRoutes.LOGOUT.value)
async def logout(token: str = Depends(oauth2_scheme)):
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    jti = payload['jti']
    exp = payload['exp']

    now = datetime.now(timezone.utc).timestamp()
    ttl = int(exp - now)

    if ttl > 0:
        redis_client.setex(
            f"blacklist:jti:{jti}",
            ttl,
            1
        )

    return {"detail": "Successfully logged out"}


''' GET TOKEN 🔑 '''
@router.get(AuthRoutes.TOKEN.value)
async def get_token(token: Annotated[str, Depends(oauth2_scheme)]):
    return {'token': token}


''' SIGN UP 🧑‍💻 '''
@router.post(AuthRoutes.SIGNUP.value, response_model=SignUpResponse)
async def signup(signup_data: SignUp, session: SessionDep):

    if signup_data.user.password != signup_data.user.confirm_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail={'message': 'Password and Confirm password mismatch'})

    # 1️⃣ Create user (super admin)
    organization = OrganizationModel.model_validate(signup_data.organization)

    session.add(organization)
    session.commit()
    session.refresh(organization)

    # 2️⃣ Create user (super admin)
    user = UserModel.model_validate(
        signup_data.user,
        update={
            'org_id': organization.id,
            'password': PasswordHasher.create(signup_data.user.password)
        },
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    token_data = create_access_token(user.id)
    token = TokenData(access_token=token_data.access_token)

    return SignUpResponse(
        organization=OrganizationRead.model_validate(
            organization,
            from_attributes=True
        ),
        user=UserRead.model_validate(
            user,
            from_attributes=True
        ),
        token=token)



@router.post(AuthRoutes.REQUEST_OTP.value, response_model=OTPResponse)
async def request_otp(payload: OTPRequestSchema):

    otp_secret = os.getenv('OTP_SECRET')

    try:
        otp = await OtpService.request_otp(
            # tenant_id=payload.tenant_id,
            purpose=payload.purpose,
            identifier=payload.identifier,
            otp_secret=otp_secret,
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    except Exception:
        logging.exception("OTP error")
        raise HTTPException(status_code=500, detail="Internal error")

    if IS_DEV:
        return OTPResponse(message="OTP generated successfully", otp=otp) 
    
    return OTPResponse(message="OTP generated successfully")



@router.post(AuthRoutes.VERIFY_OTP.value, response_model=OTPResponse)
async def verify_otp(
    payload: OTPVerifySchema,
    session: SessionDep):

    otp_secret = os.getenv('OTP_SECRET')

    try:
        is_valid = await OtpService.verify_otp(
            # tenant_id=payload.tenant_id,
            purpose=payload.purpose,
            identifier=payload.identifier,
            otp=payload.otp,
            otp_secret=otp_secret,
        )

        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail="Invalid OTP"
            )

        # 👉 Activate user (example for signup flow)
        if payload.purpose == "signup":
            user = session.exec(
                select(UserModel).where(UserModel.email == payload.identifier)
            ).first()

            if not user:
                raise HTTPException(
                    status_code=404,
                    detail="User not found"
                )

            user.verified = True
            session.add(user)
            session.commit()

    except ValueError as e:
        raise HTTPException(
            status_code=429,
            detail=str(e)
        )

    except HTTPException:
        raise

    except Exception:
        logging.exception("OTP verification error")
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )

    return OTPVerifyResponse(
    message="OTP verified successfully",
    verified=True)


@router.get("/users/me/", response_model=User)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user


'''
If you want next, I can help you:

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