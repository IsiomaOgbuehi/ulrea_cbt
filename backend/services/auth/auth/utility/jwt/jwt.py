import jwt
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
import os
from auth.api_models.token import Token
from auth.core.settings import settings


JWT_SECRET = settings.JWT_SECRET
JWT_ALGORITHM = settings.JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = float(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
REFRESH_TOKEN_EXPIRE_DAYS = int(settings.REFRESH_TOKEN_EXPIRE_DAYS)

def create_access_token(subject: UUID) -> Token:
    jti = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        'sub': str(subject),
        'jti': jti,
        'exp': expires_at,
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return Token(
        access_token=token,
        jti=jti,
        expires_at=expires_at
    )

def create_refresh_token(subject: UUID) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload = {
        'sub': str(subject),
        'jti': str(uuid4()),  # unique id so it can be blacklisted individually
        'exp': expires_at,
        'type': 'refresh',   # distinguish from access token
    }

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_refresh_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    if payload.get('type') != 'refresh':
        raise ValueError("Not a refresh token")

    return payload
    