from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

from auth.core.settings import settings

# Hosted Redis drops idle connections without telling the client; a command
# sent on one of those waits for the full socket timeout and fails. So:
# - health_check_interval pings a connection that has been idle before reusing it,
# - socket_keepalive keeps the TCP connection from looking idle,
# - retry reconnects and tries again (twice, quickly) on a dropped or timed-out connection.
redis_client: Redis = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
    socket_keepalive=True,
    health_check_interval=30,
    retry=Retry(ExponentialBackoff(cap=0.5, base=0.05), retries=2),
    retry_on_error=[RedisConnectionError, RedisTimeoutError],
)
