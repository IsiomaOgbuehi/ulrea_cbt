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

def create_access_token(
    subject: UUID, org_id: UUID, role: str, perms: dict[str, str] | None = None
) -> Token:
    """
    perms: the member's effective permissions ({"exams.view": "assigned", ...}),
    read by other services so they don't need to ask this one.
    """
    jti = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=ACCESS_TOKEN_EXPIRE_MINUTES
    )

    payload = {
        'sub': str(subject),
        'jti': jti,
        'org_id': str(org_id),
        'role': role,
        'exp': expires_at,
        'type': 'access',
    }
    if perms is not None:
        payload['perms'] = perms

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return Token(
        access_token=token,
        jti=jti,
        expires_at=expires_at
    )

def create_refresh_token(subject: UUID, org_id: UUID | None = None) -> str:
    """org_id: keep refreshing into this organisation (people can belong to several)."""
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    payload: dict[str, object] = {
        'sub': str(subject),
        'jti': str(uuid4()),  # unique id so it can be blacklisted individually
        'exp': expires_at,
        'type': 'refresh',   # distinguish from access token
    }
    if org_id is not None:
        payload['org_id'] = str(org_id)

    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    if payload.get('type') != 'access':
        raise ValueError("Not an access token")

    return payload


def decode_refresh_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    if payload.get('type') != 'refresh':
        raise ValueError("Not a refresh token")

    return payload
    



def create_student_provisional_token(subject: UUID) -> Token:
    """
    Issued after email verification for students with no org yet.
    Encodes no org_id or role — only used to reach /student/organizations/subscribe.
    """
    jti = str(uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        'sub': str(subject),
        'jti': jti,
        'type': 'provisional',   # ← distinct type, not 'access'
        'exp': expires_at,
    }

    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    return Token(
        access_token=token,
        jti=jti,
        expires_at=expires_at,
    )


def decode_provisional_token(token: str) -> dict:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

    if payload.get('type') != 'provisional':
        raise ValueError("Not a provisional token")
    
    return payload