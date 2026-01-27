import jwt
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from dotenv import load_dotenv
import os
from auth.api_models.token import Token

load_dotenv()

JWT_SECRET = os.getenv('JWT_SECRET')
JWT_ALGORITHM = os.getenv('JWT_ALGORITHM')
ACCESS_TOKEN_EXPIRE_MINUTES = float(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES'))

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
    