from fastapi import HTTPException
import httpx
import json
from uuid import UUID
import time
from typing import ClassVar


from pydantic import BaseModel
from cbt_service.core.http_client import get_http_client
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

    CACHE_TTL_SECONDS: int = 300
    REQUEST_TIMEOUT_SECONDS: float = 7.0
    _REDIS_COOLDOWN_SECONDS: ClassVar[float] = 30.0
    _redis_down_until: ClassVar[float] = 0.0   # monotonic timestamp; shared by all instances

    # ---- Redis circuit breaker -------------------------------------------

    @classmethod
    def _redis_available(cls) -> bool:
        return time.monotonic() >= cls._redis_down_until

    @classmethod
    def _trip_redis_breaker(cls) -> None:
        cls._redis_down_until = time.monotonic() + cls._REDIS_COOLDOWN_SECONDS

    async def _cache_get(self, key: str) -> str | bytes | None:
        if not self._redis_available():
            return None
        try:
            return await self.redis.get(key)
        except Exception:
            self._trip_redis_breaker()
            return None

    async def _cache_mget(self, keys: list[str]) -> list[str | bytes | None]:
        if not keys or not self._redis_available():
            return [None] * len(keys)
        try:
            return list(await self.redis.mget(keys))
        except Exception:
            self._trip_redis_breaker()
            return [None] * len(keys)

    async def _cache_set(self, key: str, value: str, ex: int) -> None:
        if not self._redis_available():
            return
        try:
            await self.redis.set(key, value, ex=ex)
        except Exception:
            self._trip_redis_breaker()

    async def _cache_set_many(self, items: list[tuple[str, str]], ex: int) -> None:
        if not items or not self._redis_available():
            return
        try:
            async with self.redis.pipeline(transaction=False) as pipe:
                for key, value in items:
                    pipe.set(key, value, ex=ex)
                await pipe.execute()
        except Exception:
            self._trip_redis_breaker()




    def __init__(self, redis_client, base_url: str | None = None):
        # Calls below add /api/v1 themselves, so accept the URL with or without it.
        url: str = (base_url or settings.AUTH_SERVICE_URL).rstrip("/")
        self.base_url = url[: -len("/api/v1")] if url.endswith("/api/v1") else url
        self.redis = redis_client

    @staticmethod
    def _cache_key(user_id: UUID) -> str:
        return f"user_cache:{user_id}"
    

    # async def get_user(self, user_id: UUID) -> UserSummary | None:
    #     """Fetch one user, using Redis before calling auth service."""

    #     cache_key = self._cache_key(user_id)

    #     try:
    #         cached = await self.redis.get(cache_key)

    #         if cached:
    #             return UserSummary(**json.loads(cached))

    #     except Exception:
    #         # A Redis failure should not prevent a direct auth lookup.
    #         pass

    #     try:
    #         async with httpx.AsyncClient(
    #             timeout=self.REQUEST_TIMEOUT_SECONDS
    #         ) as client:
    #             response = await client.get(
    #                 f"{self.base_url}/api/v1/internal/users/{user_id}",
    #                 headers={
    #                     "X-Internal-Secret": settings.INTERNAL_SECRET,
    #                 },
    #             )

    #             if response.status_code == 404:
    #                 return None

    #             response.raise_for_status()
    #             user = UserSummary(**response.json())

    #     except (httpx.HTTPError, ValueError):
    #         return None

    #     try:
    #         await self.redis.set(
    #             cache_key,
    #             json.dumps(user.model_dump(mode="json")),
    #             ex=self.CACHE_TTL_SECONDS,
    #         )
    #     except Exception:
    #         # The user lookup succeeded; caching is optional.
    #         pass

    #     return user

    async def get_user(self, user_id: UUID) -> UserSummary | None:
        cache_key: str = self._cache_key(user_id)

        cached = await self._cache_get(cache_key)
        if cached:
            try:
                return UserSummary(**json.loads(cached))
            except (ValueError, TypeError):
                pass  # malformed entry: refetch

        try:
            response: httpx.Response = await get_http_client().get(
                f"{self.base_url}/api/v1/internal/users/{user_id}",
                headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            user: UserSummary = UserSummary(**response.json())
        except (httpx.HTTPError, ValueError):
            return None

        await self._cache_set(
            cache_key, json.dumps(user.model_dump(mode="json")), self.CACHE_TTL_SECONDS
        )
        return user

    

    # async def get_users_bulk(
    #     self,
    #     user_ids: list[UUID],
    #     org_id: UUID
    # ) -> dict[str, UserSummary]:
    #     """
    #     Fetch multiple users in one request.

    #     Cached users are returned immediately. Uncached IDs are fetched
    #     in one bulk request.

    #     Pass org_id when retrieving users for a particular organization.
    #     """

    #     results: dict[str, UserSummary] = {}

    #     # Remove duplicates while preserving order.
    #     unique_ids = list(dict.fromkeys(user_ids))

    #     if not unique_ids:
    #         return results

    #     uncached_ids: list[UUID] = []

    #     # Check Redis cache.
    #     for user_id in unique_ids:
    #         cache_key = self._cache_key(user_id)

    #         try:
    #             cached = await self.redis.get(cache_key)
    #         except Exception:
    #             # Treat Redis errors as cache misses.
    #             cached = None

    #         if cached:
    #             try:
    #                 results[str(user_id)] = UserSummary(
    #                     **json.loads(cached)
    #                 )
    #                 continue
    #             except (ValueError, TypeError):
    #                 # Ignore malformed cached data and fetch it again.
    #                 pass

    #         uncached_ids.append(user_id)

    #     if not uncached_ids:
    #         return results

    #     payload = {
    #         "user_ids": [str(user_id) for user_id in uncached_ids],
    #     }

    #     if org_id is not None:
    #         payload["org_id"] = str(org_id)

    #     try:
    #         async with httpx.AsyncClient(
    #             timeout=self.REQUEST_TIMEOUT_SECONDS
    #         ) as client:
    #             response = await client.post(
    #                 f"{self.base_url}/api/v1/internal/users/bulk",
    #                 json=payload,
    #                 headers={
    #                     "X-Internal-Secret": settings.INTERNAL_SECRET,
    #                 },
    #             )

    #             response.raise_for_status()

    #             for user_data in response.json():
    #                 user = UserSummary(**user_data)
    #                 user_id = str(user.id)

    #                 results[user_id] = user

    #                 try:
    #                     await self.redis.set(
    #                         self._cache_key(user.id),
    #                         json.dumps(user.model_dump(mode="json")),
    #                         ex=self.CACHE_TTL_SECONDS,
    #                     )
    #                 except Exception:
    #                     # Do not discard successful API results if Redis fails.
    #                     pass

    #     except (httpx.HTTPError, ValueError, TypeError):
    #         # Return whatever was available from cache.
    #         pass

    #     return results


    async def get_users_bulk(
    self,
    user_ids: list[UUID],
    org_id: UUID | None = None,
) -> dict[str, UserSummary]:
        results: dict[str, UserSummary] = {}
        unique_ids: list[UUID] = list(dict.fromkeys(user_ids))
        if not unique_ids:
            return results

        cached_values: list[str | bytes | None] = await self._cache_mget(
            [self._cache_key(u) for u in unique_ids]
        )

        uncached_ids: list[UUID] = []
        for user_id, cached in zip(unique_ids, cached_values):
            if cached:
                try:
                    results[str(user_id)] = UserSummary(**json.loads(cached))
                    continue
                except (ValueError, TypeError):
                    pass
            uncached_ids.append(user_id)

        if not uncached_ids:
            return results

        payload: dict[str, object] = {"user_ids": [str(u) for u in uncached_ids]}
        if org_id is not None:
            payload["org_id"] = str(org_id)

        try:
            response: httpx.Response = await get_http_client().post(
                f"{self.base_url}/api/v1/internal/users/bulk",
                json=payload,
                headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            fetched: list[UserSummary] = [UserSummary(**d) for d in response.json()]
        except (httpx.HTTPError, ValueError, TypeError):
            return results

        for user in fetched:
            results[str(user.id)] = user

        await self._cache_set_many(
            [(self._cache_key(u.id), json.dumps(u.model_dump(mode="json"))) for u in fetched],
            self.CACHE_TTL_SECONDS,
        )
        return results


    # --------------------------------------------------------
    # COHORT LOOKUPS
    # --------------------------------------------------------

    # async def get_cohort_student_ids(
    #     self,
    #     cohort_id: UUID,
    #     org_id: UUID,
    # ) -> list[UUID]:
    #     """
    #     Fetch active student IDs for a cohort.
    #     Called before assigning an exam to a whole cohort.
    #     Raises HTTPException if cohort is graduated.
    #     """
    #     cache_key = f"cohort_students:{cohort_id}"

    #     cached = await self.redis.get(cache_key)
    #     if cached:
    #         return [UUID(i) for i in json.loads(cached)]

    #     try:
    #         async with httpx.AsyncClient() as client:
    #             response = await client.get(
    #                 f"{self.base_url}/api/v1/internal/cohorts/{cohort_id}/student_ids",
    #                 headers={
    #                     "X-Internal-Secret": settings.INTERNAL_SECRET,
    #                     "X-Org-Id": str(org_id),
    #                 },
    #                 timeout=5.0,
    #             )

    #             if response.status_code == 200:
    #                 ids = response.json()
    #                 # Cache for 2 minutes — cohort membership changes infrequently
    #                 await self.redis.set(
    #                     cache_key,
    #                     json.dumps(ids),
    #                     ex=120,
    #                 )
    #                 return [UUID(i) for i in ids]

    #             if response.status_code == 400:
    #                 # Graduated cohort — surface the error
    #                 from fastapi import HTTPException
    #                 detail = response.json().get("detail", "Cohort is not available for exam assignment.")
    #                 raise HTTPException(status_code=400, detail=detail)

    #             if response.status_code == 404:
    #                 from fastapi import HTTPException
    #                 raise HTTPException(status_code=404, detail="Cohort not found.")

    #             return []

    #     except httpx.TimeoutException:
    #         from fastapi import HTTPException
    #         raise HTTPException(
    #             status_code=502,
    #             detail="Auth service timed out fetching cohort members."
            # )

    async def get_cohort_student_ids(self, cohort_id: UUID, org_id: UUID) -> list[UUID]:
        """
        Active student IDs in a cohort, for assigning an exam to it. Not cached:
        it's an occasional admin action, and a cached list would miss students
        just added or removed, and a cohort just graduated.
        """
        try:
            response: httpx.Response = await get_http_client().get(
                f"{self.base_url}/api/v1/internal/cohorts/{cohort_id}/student_ids",
                headers={
                    "X-Internal-Secret": settings.INTERNAL_SECRET,
                    "X-Org-Id": str(org_id),
                },
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            raise HTTPException(status_code=502, detail="Auth service timed out fetching cohort members.")
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="Could not reach auth service.")

        if response.status_code == 400:
            detail: str = response.json().get("detail", "Cohort is not available for exam assignment.")
            raise HTTPException(status_code=400, detail=detail)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Cohort not found.")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Unexpected response from auth service.")

        ids: list[str] = response.json()
        return [UUID(i) for i in ids]

    


    # async def filter_student_ids(
    #     self,
    #     student_ids: list[UUID],
    #     org_id: UUID,
    # ) -> list[UUID]:
    #     """
    #     Return the subset of student_ids that are active students in org_id.
    #     Raises on any auth-service failure, so the caller can tell
    #     "service unavailable" (502) apart from "no valid students" (422).
    #     Deliberately not cached: membership changes should apply immediately.
    #     """
    #     unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
    #     if not unique_ids:
    #         return []

    #     try:
    #         async with httpx.AsyncClient(timeout=self.REQUEST_TIMEOUT_SECONDS) as client:
    #             response: httpx.Response = await client.post(
    #                 f"{self.base_url}/api/v1/internal/students/filter",
    #                 json={
    #                     "user_ids": [str(i) for i in unique_ids],
    #                     "org_id": str(org_id),
    #                 },
    #                 headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
    #             )
    #             response.raise_for_status()
    #             return [UUID(i) for i in response.json()]
    #     except (httpx.HTTPError, ValueError) as exc:
    #         raise RuntimeError("Could not verify students with auth service.") from exc


    async def filter_student_ids(self, student_ids: list[UUID], org_id: UUID) -> list[UUID]:
        unique_ids: list[UUID] = list(dict.fromkeys(student_ids))
        if not unique_ids:
            return []

        try:
            response: httpx.Response = await get_http_client().post(
                f"{self.base_url}/api/v1/internal/students/filter",
                json={"user_ids": [str(i) for i in unique_ids], "org_id": str(org_id)},
                headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                timeout=self.REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            return [UUID(i) for i in response.json()]
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("Could not verify students with auth service.") from exc