"""
Revoked (logged-out) tokens, kept in Redis until they would expire anyway.

Redis being slow or unreachable must not take sign-in down with it:
- Access-token checks fail open: the token's signature and expiry are still
  checked, only the logout list can't be, and access tokens are short-lived.
- Refresh-token checks fail closed with a 503 (try again), since a refresh
  token lives for days.
- Revoking on logout is best effort and logged; the client drops its tokens anyway.
After a failure Redis is skipped for a short cool-down, so an outage doesn't
make every request wait for the timeout first.
"""
import asyncio
import json
import logging
import time

from fastapi import HTTPException
from redis.exceptions import RedisError

from auth.utility.redis import redis_client as redis_module

logger: logging.Logger = logging.getLogger(__name__)

_COOLDOWN_SECONDS: float = 15.0
_down_until: float = 0.0


def _key(jti: str) -> str:
    return f"blacklist:jti:{jti}"


def _available() -> bool:
    return time.monotonic() >= _down_until


def _trip(error: Exception, action: str) -> None:
    global _down_until
    _down_until = time.monotonic() + _COOLDOWN_SECONDS
    logger.warning("Redis unavailable while %s token blacklist: %r", action, error)


async def is_revoked(jti: str, *, fail_open: bool) -> bool:
    """Whether the token with this jti was revoked."""
    if _available():
        try:
            return bool(await redis_module.redis_client.get(_key(jti)))
        except (RedisError, OSError) as e:
            _trip(e, "checking")
    if fail_open:
        return False
    raise HTTPException(status_code=503, detail="Sign-in service is busy. Please try again in a moment.")


async def revoke(jti: str, ttl_seconds: int) -> None:
    """Revoke a token for its remaining lifetime (best effort)."""
    if ttl_seconds <= 0:
        return
    try:
        await redis_module.redis_client.set(_key(jti), 1, ex=ttl_seconds)
    except (RedisError, OSError) as e:
        _trip(e, "updating")
        logger.error("Could not revoke token %s; it stays valid until it expires", jti)


# ------------------------------------------------------------ refresh rotation
#
# Every refresh token works once. Using it returns a new access token and a new
# refresh token, and the old one is revoked. If an already-used refresh token
# comes back, someone kept a copy: the whole sign-in session is ended. The one
# exception is a grace period: two tabs refreshing at once with the same
# token, or a reply lost on a bad connection (the server rotated, the browser
# never got the new token and tries again with the old one), get the same new
# pair. It's long enough to ride out a flaky connection mid-exam.

ROTATION_GRACE_SECONDS: int = 10 * 60


def _session_key(sid: str) -> str:
    return f"session:revoked:{sid}"


def _rotation_key(jti: str) -> str:
    return f"refresh:rotated:{jti}"


def _busy() -> HTTPException:
    return HTTPException(status_code=503, detail="Sign-in service is busy. Please try again in a moment.")


async def claim_refresh(jti: str, ttl_seconds: int) -> bool:
    """Mark a refresh token used. True if this call was first; False if it was already used."""
    if not _available():
        raise _busy()
    try:
        first = await redis_module.redis_client.set(_key(jti), 1, ex=max(1, ttl_seconds), nx=True)
        return bool(first)
    except (RedisError, OSError) as e:
        _trip(e, "updating")
        raise _busy()


async def session_revoked(sid: str) -> bool:
    if not _available():
        raise _busy()
    try:
        return bool(await redis_module.redis_client.get(_session_key(sid)))
    except (RedisError, OSError) as e:
        _trip(e, "checking")
        raise _busy()


async def revoke_session(sid: str, ttl_seconds: int) -> None:
    """End a sign-in session: none of its refresh tokens work any more (best effort)."""
    if ttl_seconds <= 0:
        return
    try:
        await redis_module.redis_client.set(_session_key(sid), 1, ex=ttl_seconds)
    except (RedisError, OSError) as e:
        _trip(e, "updating")
        logger.error("Could not end session %s", sid)


async def remember_rotation(jti: str, tokens: dict[str, str]) -> None:
    try:
        await redis_module.redis_client.set(_rotation_key(jti), json.dumps(tokens), ex=ROTATION_GRACE_SECONDS)
    except (RedisError, OSError) as e:
        _trip(e, "updating")


async def rotation_for(jti: str, wait_seconds: float = 1.0) -> dict[str, str] | None:
    """The pair a just-used refresh token was exchanged for, if within the grace
    period (waits briefly in case the other request is still finishing)."""
    deadline: float = time.monotonic() + wait_seconds
    while True:
        try:
            raw = await redis_module.redis_client.get(_rotation_key(jti))
        except (RedisError, OSError) as e:
            _trip(e, "checking")
            raise _busy()
        if raw:
            data = json.loads(str(raw))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else None
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(0.1)
