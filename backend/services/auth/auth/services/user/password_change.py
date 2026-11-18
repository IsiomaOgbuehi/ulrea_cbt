"""
Changing your own password while signed in.

Wrong current passwords are counted per person in Redis: after
MAX_FAILURES within FAILURE_WINDOW_SECONDS, further tries are refused until
the window passes. The count is best effort; if Redis is down, the password
check still runs, only the limit is skipped.
"""
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from redis.exceptions import RedisError
from sqlmodel import Session

from auth.database.schema.user.user_db import UserModel
from auth.utility.password.password_harsher import PasswordHasher
from auth.utility.redis import redis_client as redis_module

logger: logging.Logger = logging.getLogger(__name__)

MIN_LENGTH: int = 8
MAX_FAILURES: int = 5
FAILURE_WINDOW_SECONDS: int = 15 * 60


class WrongPassword(Exception):
    """The current password given doesn't match."""


def _failures_key(user_id: UUID) -> str:
    return f"password-change:failures:{user_id}"


def validate_new(new_password: str, confirm_password: str) -> None:
    if len(new_password) < MIN_LENGTH:
        raise HTTPException(status_code=422, detail=f"Use at least {MIN_LENGTH} characters for your password.")
    if new_password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")


def change(
    session: Session,
    user: UserModel,
    current_password: str,
    new_password: str,
    confirm_password: str,
) -> None:
    """Check the current password, then save the new one. Raises WrongPassword."""
    if not user.password:
        raise HTTPException(
            status_code=400,
            detail="Your account signs in without a password, so there's none to change.",
        )
    validate_new(new_password, confirm_password)
    if not PasswordHasher.verify(current_password, user.password):
        raise WrongPassword()
    if PasswordHasher.verify(new_password, user.password):
        raise HTTPException(status_code=400, detail="Choose a password different from your current one.")
    user.password = PasswordHasher.create(new_password)
    user.password_changed_at = datetime.now(UTC)
    user.updated_at = datetime.now(UTC)
    session.add(user)
    session.commit()
    session.refresh(user)


async def check_not_locked(user_id: UUID) -> None:
    try:
        raw = await redis_module.redis_client.get(_failures_key(user_id))
    except (RedisError, OSError) as e:
        logger.warning("Couldn't read password-change failures: %r", e)
        return
    if raw is not None and int(raw) >= MAX_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="Too many wrong passwords. Try again in 15 minutes, or sign out and use 'Forgot password'.",
        )


async def record_failure(user_id: UUID) -> None:
    key = _failures_key(user_id)
    try:
        count = await redis_module.redis_client.incr(key)
        if int(count) == 1:
            await redis_module.redis_client.expire(key, FAILURE_WINDOW_SECONDS)
    except (RedisError, OSError) as e:
        logger.warning("Couldn't count a password-change failure: %r", e)


async def clear_failures(user_id: UUID) -> None:
    try:
        await redis_module.redis_client.delete(_failures_key(user_id))
    except (RedisError, OSError) as e:
        logger.warning("Couldn't clear password-change failures: %r", e)
