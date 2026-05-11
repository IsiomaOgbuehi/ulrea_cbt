import json
import hmac
import hashlib
import secrets

from auth.core.settings import settings
from auth.utility.redis.redis_client import redis_client

IS_DEV = settings.ENVIRONMENT == "dev"

OTP_TTL_SECONDS = 300
OTP_REUSE_THRESHOLD_SECONDS = 90   # must wait this long before requesting a new one
RATE_LIMIT_WINDOW = 600
MAX_REQUESTS = 3
MAX_ATTEMPTS = 5


class OtpService:
    _secret: str = settings.OTP_SECRET

    @classmethod
    async def request_otp(cls, purpose: str, identifier: str):
        identifier = identifier.strip().lower()
        key = cls._otp_key(purpose, identifier)

        # Check if an active OTP already exists
        existing_raw = await redis_client.get(key)

        if existing_raw:
            existing_data = json.loads(existing_raw)
            ttl = await redis_client.ttl(key)
            time_elapsed = OTP_TTL_SECONDS - ttl

            if time_elapsed < OTP_REUSE_THRESHOLD_SECONDS:
                # Too soon — block resend and tell frontend how long to wait
                if not IS_DEV:
                    wait = OTP_REUSE_THRESHOLD_SECONDS - time_elapsed
                    raise ValueError(
                        f"Please wait {int(wait)} seconds before requesting a new code."
                    )

                # In dev — return existing OTP so tests aren't blocked
                return existing_data.get("otp_dev")

            # Threshold passed — fall through and generate a new OTP below

        # Rate limit only applies to new OTP generation
        await cls._check_rate_limit(purpose, identifier)

        otp = cls._generate_otp()

        payload = {
            "otp_hash": cls._hash_otp(otp, cls._secret),
            "attempts": 0,
            "max_attempts": MAX_ATTEMPTS,
            "otp_dev": otp if IS_DEV else None,  # plaintext only stored in dev
        }

        await redis_client.set(key, json.dumps(payload), ex=OTP_TTL_SECONDS)

        return otp # if IS_DEV else None

    @classmethod
    async def verify_otp(cls, purpose: str, identifier: str, otp: str):
        identifier = identifier.strip().lower()
        key = cls._otp_key(purpose, identifier)

        raw = await redis_client.get(key)

        if not raw:
            return False

        data = json.loads(raw)

        if data["attempts"] >= data["max_attempts"]:
            await redis_client.delete(key)
            raise ValueError("Too many attempts")

        expected = data["otp_hash"]
        provided = cls._hash_otp(otp, cls._secret)

        if not hmac.compare_digest(expected, provided):
            data["attempts"] += 1
            ttl = await redis_client.ttl(key)
            await redis_client.set(key, json.dumps(data), ex=ttl)
            return False

        await redis_client.delete(key)
        return True

    @classmethod
    async def get_active_otp(cls, purpose: str, identifier: str):
        """Check whether an active OTP exists and how long until it expires."""
        identifier = identifier.strip().lower()
        key = cls._otp_key(purpose, identifier)
        raw = await redis_client.get(key)

        if not raw:
            return None

        ttl = await redis_client.ttl(key)
        time_elapsed = OTP_TTL_SECONDS - ttl

        return {
            "exists": True,
            "expires_in": ttl,
            "resend_available_in": max(0, OTP_REUSE_THRESHOLD_SECONDS - time_elapsed),
        }

    @staticmethod
    def _generate_otp(length: int = 6) -> str:
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    @staticmethod
    def _hash_otp(otp: str, secret: str) -> str:
        if not secret:
            raise ValueError("OTP_SECRET is not configured")
        return hmac.new(
            key=secret.encode(),
            msg=otp.encode(),
            digestmod=hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _otp_key(purpose: str, identifier: str):
        identifier = identifier.strip().lower()
        return f"otp:{purpose}:{identifier}"

    @classmethod
    async def _check_rate_limit(cls, purpose: str, identifier: str):
        if IS_DEV:
            return
        key = f"otp_rl:{purpose}:{identifier.strip().lower()}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, RATE_LIMIT_WINDOW)
        if count > MAX_REQUESTS:
            raise ValueError("Too many OTP requests")

    @classmethod
    async def invalidate_otp(cls, purpose: str, identifier: str):
        key = cls._otp_key(purpose, identifier)
        await redis_client.delete(key)
        rl_key = f"otp_rl:{purpose}:{identifier.strip().lower()}"
        current = await redis_client.get(rl_key)
        if current and int(current) > 0:
            await redis_client.decr(rl_key)



# import hashlib
# import secrets
# import hmac
# import json
# from redis.asyncio import Redis
# from auth.core.settings import settings
# from auth.utility.redis.redis_client import redis_client as _redis

# IS_DEV = settings.ENVIRONMENT == "dev"

# OTP_TTL_SECONDS = 300    # OTP valid for 5 minutes
# OTP_REUSE_THRESHOLD_SECONDS = 90  # can request new OTP after 90 seconds
# RATE_LIMIT_WINDOW = 600
# MAX_REQUESTS = 3
# MAX_ATTEMPTS = 5

# redis_client = _redis # Redis(decode_responses=True)


# class OtpService:
#     _secret: str = settings.OTP_SECRET

#     @classmethod
#     async def request_otp(cls, purpose: str, identifier: str):
#         identifier = identifier.strip().lower()
#         await cls._check_rate_limit(purpose, identifier)

#         key = cls._otp_key(purpose, identifier)
#         raw = await redis_client.get(key)

#         if raw:
#             ttl = await redis_client.ttl(key)
#             data = json.loads(raw)
#             time_elapsed = OTP_TTL_SECONDS - ttl  # how long since it was created

#             if time_elapsed < OTP_REUSE_THRESHOLD_SECONDS:
#                 # Too soon — tell frontend how long to wait
#                 if not IS_DEV:  # ← only enforce in production
#                     wait = OTP_REUSE_THRESHOLD_SECONDS - time_elapsed
#                     raise ValueError(f"Please wait {wait} seconds before requesting a new code.")

#         # Generate fresh OTP
#         otp = cls._generate_otp()
#         payload = {
#             "otp_hash": cls._hash_otp(otp, cls._secret),
#             "attempts": 0,
#             "max_attempts": MAX_ATTEMPTS,
#             "otp_plain": otp if IS_DEV else None,
#         }
#         await redis_client.set(key, json.dumps(payload), ex=OTP_TTL_SECONDS)
#         return otp # if IS_DEV else None
    

#     @classmethod
#     async def verify_otp(
#         cls,
#         purpose: str,
#         identifier: str,
#         otp: str,
#     ):
#         identifier = identifier.strip().lower()
#         key = cls._otp_key(purpose, identifier)

#         raw = await redis_client.get(key)

#         if not raw:
#             return False

#         data = json.loads(raw)

#         if data["attempts"] >= data["max_attempts"]:
#             await redis_client.delete(key)
#             raise ValueError("Too many attempts")

#         expected = data["otp_hash"]
#         provided = cls._hash_otp(otp, cls._secret)

#         if not hmac.compare_digest(expected, provided):
#             data["attempts"] += 1
#             ttl = await redis_client.ttl(key)
#             await redis_client.set(key, json.dumps(data), ex=ttl)
#             return False

#         await redis_client.delete(key)
#         return True

#     @staticmethod
#     def _generate_otp(length: int = 6) -> str:
#         return "".join(str(secrets.randbelow(10)) for _ in range(length))

#     @staticmethod
#     def _hash_otp(otp: str, secret: str) -> str:
#         if not secret:
#             raise ValueError("OTP_SECRET is not configured")  # ← fail loud, not silent
#         return hmac.new(
#             key=secret.encode(),
#             msg=otp.encode(),
#             digestmod=hashlib.sha256
#         ).hexdigest()
    
    
#     @staticmethod
#     def _otp_key(purpose: str, identifier: str):
#         identifier = identifier.strip().lower()
#         return f"otp:{purpose}:{identifier}"
    
#     @staticmethod
#     def _verify_otp_key(purpose: str, identifier: str):
#         identifier = identifier.strip().lower()
#         return f"otp_rl_verify:{purpose}:{identifier}"

#     # @staticmethod
#     # def _otp_key(tenant_id: str, purpose: str, identifier: str):
#     #     return f"otp:{tenant_id}:{purpose}:{identifier}"

#     @classmethod
#     async def _check_rate_limit(cls, purpose: str, identifier: str):
#         if IS_DEV:
#             return # 🚀 skip in tests
#         key = f"otp_rl:{purpose}:{identifier.strip().lower()}"

#         count = await redis_client.incr(key)

#         if count == 1:
#             await redis_client.expire(key, RATE_LIMIT_WINDOW)

#         if count > MAX_REQUESTS:
#             raise ValueError("Too many OTP requests")
        
    
#     @classmethod
#     async def invalidate_otp(cls, purpose: str, identifier: str):
#         key = cls._otp_key(purpose, identifier)
#         await redis_client.delete(key)
#         # Also clear the rate limit increment so the failed attempt doesn't count
#         rl_key = f"otp_rl:{purpose}:{identifier.strip().lower()}"
#         await redis_client.decr(rl_key)