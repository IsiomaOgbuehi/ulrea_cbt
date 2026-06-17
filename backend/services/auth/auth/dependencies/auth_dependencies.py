from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt.exceptions import InvalidTokenError
from auth.api_models.user import UserInDB
from auth.api_models.token import TokenData
from auth.api.v1.auth_routes import AuthRoutes
from sqlmodel import Session, select
from auth.core.settings import settings
from auth.database.database import SessionDep
from auth.database.schema.user.user_db import UserModel
from auth.utility.redis.redis_client import redis_client
from auth.database.schema.membership.membership_db import OrgMembership
from auth.database.schema.user.enums import MembershipStatus, UserRole
from auth.services.user.user_context import UserContext
from auth.utility.jwt.jwt import decode_access_token, decode_provisional_token


SECRET_KEY = settings.SECRET_KEY
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = 30


oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f'{AuthRoutes.API_VERSION.value}{AuthRoutes.BASE_ROUTE.value}{AuthRoutes.LOGIN.value}')

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: SessionDep,
) -> UserModel:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise credentials_exception

    # Reject refresh tokens used as access tokens
    if payload.get('type') != 'access':
        raise credentials_exception

    # Check blacklist — covers logged out tokens
    jti = payload.get('jti')
    if not jti or await redis_client.get(f"blacklist:jti:{jti}"):
        raise HTTPException(status_code=401, detail="Token has been revoked.")

    # Convert sub back to UUID for SQLAlchemy
    try:
        user_id = UUID(payload.get('sub'))
    except (ValueError, AttributeError):
        raise credentials_exception

    user = session.exec(select(UserModel).where(UserModel.id == user_id)).first()
    if not user:
        raise credentials_exception

    return user


def get_user(db, username: str):
    if username in db:
        user_dict = db[username]
        return UserInDB(**user_dict)
    


async def get_user_context(
        session: SessionDep,
        token: str = Depends(oauth2_scheme),
) -> UserContext:
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Access token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid access token.")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    # Check blacklist (same pattern as your refresh token check in /refresh endpoint)
    jti = payload.get('jti')
    is_blacklisted = await redis_client.get(f"blacklist:jti:{jti}")
    if is_blacklisted:
        raise HTTPException(status_code=401, detail="Token has been revoked.")

    user_id = UUID(payload["sub"])
    org_id  = UUID(payload["org_id"])
    role    = UserRole(payload["role"])

    user = session.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    # Exact match using org_id from token — no more .first() guessing
    membership = session.exec(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id  == org_id,
            OrgMembership.status  == MembershipStatus.ACTIVE,
        )
    ).first()

    if not membership:
        raise HTTPException(status_code=403, detail="No active membership for this organization.")

    return UserContext(user=user, membership=membership)




def get_provisional_user(
    session: SessionDep,
    token: str = Depends(oauth2_scheme),
) -> UserModel:
    """
    Dependency for endpoints that accept provisional tokens.
    Only valid for students who verified email but have no org yet.
    """
    try:
        payload = decode_provisional_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")

    user_id = UUID(payload["sub"])
    user = session.get(UserModel, user_id)

    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    if not user.verified:
        raise HTTPException(status_code=403, detail="Email not verified.")

    return user



def get_provisional_or_authenticated_user(
    session: SessionDep,
    token: str = Depends(oauth2_scheme),
) -> UserModel:
    """
    Accepts both provisional and full access tokens.
    Used for endpoints a student can reach with either token type.
    """
    # Try full access token first
    try:
        payload = decode_access_token(token)
        user_id = UUID(payload["sub"])
        user = session.get(UserModel, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found.")
        return user
    except ValueError:
        pass  # not an access token, try provisional

    # Try provisional token
    try:
        payload = decode_provisional_token(token)
        user_id = UUID(payload["sub"])
        user = session.get(UserModel, user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found.")
        if not user.verified:
            raise HTTPException(status_code=403, detail="Email not verified.")
        return user
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token.")



# def get_user_context(
#     session: SessionDep,
#     current_user: UserModel = Depends(get_current_user),
# ) -> UserContext:

#     membership = session.exec(
#         select(OrgMembership).where(
#             OrgMembership.user_id == current_user.id,
#             OrgMembership.status == MembershipStatus.ACTIVE,
#         )
#     ).first()

#     if not membership:
#         raise HTTPException(
#             status_code=403,
#             detail="No active organization membership found."
#         )

#     return UserContext(
#         user=current_user,
#         membership=membership,
#     )