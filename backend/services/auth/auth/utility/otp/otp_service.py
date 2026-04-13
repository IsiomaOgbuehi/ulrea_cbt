import hashlib
import secrets
import hmac
import json
from redis.asyncio import Redis
from auth.core.settings import settings

IS_DEV = settings.ENVIRONMENT == "dev"

OTP_TTL_SECONDS = 300
RATE_LIMIT_WINDOW = 600
MAX_REQUESTS = 3
MAX_ATTEMPTS = 5

redis_client = Redis(decode_responses=True)


class OtpService:
    _secret: str = settings.OTP_SECRET

    @classmethod
    async def request_otp(
        cls,
        purpose: str,
        identifier: str,
    ):
        identifier = identifier.strip().lower()

        await cls._check_rate_limit(purpose, identifier)

        otp = cls._generate_otp()

        key = cls._otp_key(purpose, identifier)

        payload = {
            "otp_hash": cls._hash_otp(otp, cls._secret),
            "attempts": 0,
            "max_attempts": MAX_ATTEMPTS,
        }

        await redis_client.set(key, json.dumps(payload), ex=OTP_TTL_SECONDS)

        return otp if IS_DEV else None  # ⚠️ only expose in dev
    

    @classmethod
    async def verify_otp(
        cls,
        purpose: str,
        identifier: str,
        otp: str,
    ):
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

    @staticmethod
    def _generate_otp(length: int = 6) -> str:
        return "".join(str(secrets.randbelow(10)) for _ in range(length))

    @staticmethod
    def _hash_otp(otp: str, secret: str) -> str:
        if not secret:
            raise ValueError("OTP_SECRET is not configured")  # ← fail loud, not silent
        return hmac.new(
            key=secret.encode(),
            msg=otp.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
    
    
    @staticmethod
    def _otp_key(purpose: str, identifier: str):
        identifier = identifier.strip().lower()
        return f"otp:{purpose}:{identifier}"
    
    @staticmethod
    def _verify_otp_key(purpose: str, identifier: str):
        identifier = identifier.strip().lower()
        return f"otp_rl_verify:{purpose}:{identifier}"

    # @staticmethod
    # def _otp_key(tenant_id: str, purpose: str, identifier: str):
    #     return f"otp:{tenant_id}:{purpose}:{identifier}"

    @classmethod
    async def _check_rate_limit(cls, purpose: str, identifier: str):
        if IS_DEV:
            return # 🚀 skip in tests
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
        # Also clear the rate limit increment so the failed attempt doesn't count
        rl_key = f"otp_rl:{purpose}:{identifier.strip().lower()}"
        await redis_client.decr(rl_key)