import httpx
import json
from uuid import UUID
from pydantic import BaseModel
from cbt_service.core.settings import settings
from cbt_service.schemas.item_subject_schemas import UserSummary


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
        print(f"{self.base_url}/internal/users/bulk")
        if uncached_ids:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/internal/users/bulk",
                        json={"user_ids": [str(i) for i in uncached_ids]},
                        headers={
                            "X-Internal-Secret": settings.INTERNAL_SECRET
                        },
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


    # --------------------------------------------------------
    # COHORT LOOKUPS
    # --------------------------------------------------------

    async def get_cohort_student_ids(
        self,
        cohort_id: UUID,
        org_id: UUID,
    ) -> list[UUID]:
        """
        Fetch active student IDs for a cohort.
        Called before assigning an exam to a whole cohort.
        Raises HTTPException if cohort is graduated.
        """
        cache_key = f"cohort_students:{cohort_id}"

        cached = await self.redis.get(cache_key)
        if cached:
            return [UUID(i) for i in json.loads(cached)]

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/internal/cohorts/{cohort_id}/student_ids",
                    headers={
                        "X-Internal-Secret": settings.INTERNAL_SECRET,
                        "X-Org-Id": str(org_id),
                    },
                    timeout=5.0,
                )

                if response.status_code == 200:
                    ids = response.json()
                    # Cache for 2 minutes — cohort membership changes infrequently
                    await self.redis.set(
                        cache_key,
                        json.dumps(ids),
                        ex=120,
                    )
                    return [UUID(i) for i in ids]

                if response.status_code == 400:
                    # Graduated cohort — surface the error
                    from fastapi import HTTPException
                    detail = response.json().get("detail", "Cohort is not available for exam assignment.")
                    raise HTTPException(status_code=400, detail=detail)

                if response.status_code == 404:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=404, detail="Cohort not found.")

                return []

        except httpx.TimeoutException:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=502,
                detail="Auth service timed out fetching cohort members."
            )

    async def invalidate_cohort_cache(self, cohort_id: UUID):
        """
        Call this whenever cohort membership changes
        so the exam service doesn't use stale student IDs.
        """
        await self.redis.delete(f"cohort_students:{cohort_id}")