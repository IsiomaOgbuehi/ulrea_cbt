from datetime import datetime, timedelta, timezone
from jwt import PyJWTError
import jwt

from auth.core.settings import settings


ALGORITHM = "HS256"


def create_staff_activation_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=24)

    payload = {
        "sub": str(user_id),
        "type": "staff_activation",
        "exp": expire,
    }

    return jwt.encode(
        payload,
        settings.JWT_SECRET,
        algorithm=ALGORITHM,
    )


def verify_staff_activation_token(token: str) -> str:
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[ALGORITHM],
        )

        if payload.get("type") != "staff_activation":
            raise ValueError("Invalid token type")

        user_id = payload.get("sub")

        if not user_id:
            raise ValueError("Invalid token payload")

        return user_id

    except PyJWTError:
        raise ValueError("Invalid or expired token")