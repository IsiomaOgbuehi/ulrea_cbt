import httpx
import json
from uuid import UUID
from pydantic import BaseModel
from item_bank_service.core.settings import settings
from item_bank_service.schemas.schemas import UserSummary


class AuthClient:
    """
    Internal HTTP client for calling the auth service.
    Uses Redis to cache user lookups for 5 minutes.
    """

    def __init__(self, redis_client, base_url: str = None):
        self.base_url = base_url or settings.AUTH_SERVICE_URL
        self.redis = redis_client

    async def get_user(self, user_id: UUID) -> UserSummary | None:
        cache_key = f"user_cache:{user_id}"

        # Check cache first
        cached = await self.redis.get(cache_key)
        if cached:
            return UserSummary(**json.loads(cached))

        # Call auth service
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/internal/users/{user_id}",
                    headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                    timeout=5.0,
                )
                if response.status_code == 200:
                    user = UserSummary(**response.json())
                    # Cache for 5 minutes
                    await self.redis.set(
                        cache_key,
                        json.dumps(user.model_dump(mode="json")),
                        ex=300
                    )
                    return user
                return None
        except httpx.TimeoutException:
            return None  # degrade gracefully — return None, not 500

    async def get_users_bulk(self, user_ids: list[UUID]) -> dict[str, UserSummary]:
        """Fetch multiple users in one call — avoids N+1 problem."""
        results = {}
        uncached_ids = []

        # Check cache for each
        for user_id in user_ids:
            cache_key = f"user_cache:{user_id}"
            cached = await self.redis.get(cache_key)
            if cached:
                results[str(user_id)] = UserSummary(**json.loads(cached))
            else:
                uncached_ids.append(user_id)

        # Fetch uncached in one request
        if uncached_ids:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/internal/users/bulk",
                        json={"user_ids": [str(i) for i in uncached_ids]},
                        headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                        timeout=5.0,
                    )
                    if response.status_code == 200:
                        for user_data in response.json():
                            user = UserSummary(**user_data)
                            results[str(user.id)] = user
                            # Cache each result
                            await self.redis.set(
                                f"user_cache:{user.id}",
                                json.dumps(user.model_dump(mode="json")),
                                ex=300
                            )
            except httpx.TimeoutException:
                pass  # return whatever we have from cache

        return results