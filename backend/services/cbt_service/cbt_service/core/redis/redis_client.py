from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from cbt_service.core.settings import settings

redis_client: Redis | None = None

if settings.REDIS_URL:
    # Hosted Redis drops idle connections silently: ping idle ones before use,
    # keep TCP alive, and reconnect-and-retry (twice, quickly) on a dropped one.
    redis_client = Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,    # max wait to open a connection
        socket_timeout=2,            # max wait for any command's reply
        socket_keepalive=True,
        health_check_interval=30,    # drops dead idle connections instead of hanging on them
        retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=2),
        retry_on_error=[RedisConnectionError, RedisTimeoutError],
    )
