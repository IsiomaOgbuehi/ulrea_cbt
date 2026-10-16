import httpx
import json
from uuid import UUID
from pydantic import BaseModel
from cbt_service.core.settings import settings
from cbt_service.schemas.item_subject_schemas import UserSummary



class AuthClient:
    """
    Internal HTTP client for calling the auth service.

    Features:
    - Redis caching for 5 minutes.
    - Single-user and bulk user lookups.
    - Async HTTP requests.
    - Graceful handling of timeouts and auth-service failures.
    """

    CACHE_TTL_SECONDS = 300
    REQUEST_TIMEOUT_SECONDS = 7.0

    def __init__(self, redis_client, base_url: str | None = None):
        self.base_url = (
            base_url or settings.AUTH_SERVICE_URL
        ).rstrip("/")
        self.redis = redis_client

    @staticmethod
    def _cache_key(user_id: UUID) -> str:
        return f"user_cache:{user_id}"

    async def get_user(self, user_id: UUID) -> UserSummary | None:
        """Fetch one user, using Redis before calling auth service."""

        cache_key = self._cache_key(user_id)

        try:
            cached = await self.redis.get(cache_key)

            if cached:
                return UserSummary(**json.loads(cached))

        except Exception:
            # A Redis failure should not prevent a direct auth lookup.
            pass

        try:
            async with httpx.AsyncClient(
                timeout=self.REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = await client.get(
                    f"{self.base_url}/api/v1/internal/users/{user_id}",
                    headers={
                        "X-Internal-Secret": settings.INTERNAL_SECRET,
                    },
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                user = UserSummary(**response.json())

        except (httpx.HTTPError, ValueError):
            return None

        try:
            await self.redis.set(
                cache_key,
                json.dumps(user.model_dump(mode="json")),
                ex=self.CACHE_TTL_SECONDS,
            )
        except Exception:
            # The user lookup succeeded; caching is optional.
            pass

        return user

    async def get_users_bulk(
        self,
        user_ids: list[UUID],
        org_id: UUID
    ) -> dict[str, UserSummary]:
        """
        Fetch multiple users in one request.

        Cached users are returned immediately. Uncached IDs are fetched
        in one bulk request.

        Pass org_id when retrieving users for a particular organization.
        """

        results: dict[str, UserSummary] = {}

        # Remove duplicates while preserving order.
        unique_ids = list(dict.fromkeys(user_ids))

        if not unique_ids:
            return results

        uncached_ids: list[UUID] = []

        # Check Redis cache.
        for user_id in unique_ids:
            cache_key = self._cache_key(user_id)

            try:
                cached = await self.redis.get(cache_key)
            except Exception:
                # Treat Redis errors as cache misses.
                cached = None

            if cached:
                try:
                    results[str(user_id)] = UserSummary(
                        **json.loads(cached)
                    )
                    continue
                except (ValueError, TypeError):
                    # Ignore malformed cached data and fetch it again.
                    pass

            uncached_ids.append(user_id)

        if not uncached_ids:
            return results

        payload = {
            "user_ids": [str(user_id) for user_id in uncached_ids],
        }

        if org_id is not None:
            payload["org_id"] = str(org_id)

        try:
            async with httpx.AsyncClient(
                timeout=self.REQUEST_TIMEOUT_SECONDS
            ) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/internal/users/bulk",
                    json=payload,
                    headers={
                        "X-Internal-Secret": settings.INTERNAL_SECRET,
                    },
                )

                response.raise_for_status()

                for user_data in response.json():
                    user = UserSummary(**user_data)
                    user_id = str(user.id)

                    results[user_id] = user

                    try:
                        await self.redis.set(
                            self._cache_key(user.id),
                            json.dumps(user.model_dump(mode="json")),
                            ex=self.CACHE_TTL_SECONDS,
                        )
                    except Exception:
                        # Do not discard successful API results if Redis fails.
                        pass

        except (httpx.HTTPError, ValueError, TypeError):
            # Return whatever was available from cache.
            pass

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


    async def filter_student_ids(
        self,
        student_ids: list[UUID],
        org_id: UUID,
    ) -> list[UUID]:
        """
        Return the subset of student_ids that are active students in org_id.
        Raises on any auth-service failure, so the caller can tell
        "service unavailable" (502) apart from "no valid students" (422).
        Deliberately not cached: membership changes should apply immediately.
        """
        unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
        if not unique_ids:
            return []

        try:
            async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT_SECONDS) as client:
                response: httpx.Response = await client.post(
                    f"{self.base_url}/api/v1/internal/students/filter",
                    json={
                        "user_ids": [str(i) for i in unique_ids],
                        "org_id": str(org_id),
                    },
                    headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                )
                response.raise_for_status()
                return [UUID(i) for i in response.json()]
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Could not verify students with auth service.") from exc