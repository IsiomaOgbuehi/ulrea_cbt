from redis.asyncio import Redis
from cbt_service.core.settings import settings

redis_client = None

if settings.REDIS_URL:
    redis_client = Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,    # max wait to open a connection
        socket_timeout=2,            # max wait for any command's reply
        health_check_interval=30,    # drops dead idle connections instead of hanging on them
    )