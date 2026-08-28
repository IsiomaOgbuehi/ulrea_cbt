from uuid import UUID
import httpx
from fastapi import HTTPException
from auth.core.settings import settings


class CBTServiceClient:
    """HTTP client for auth_service -> cbt_service internal calls."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0):
        self.base_url = base_url or settings.CBT_SERVICE_URL
        self.timeout = timeout
        self.headers = {"X-Internal-Secret": settings.INTERNAL_SECRET}

    async def get_assigned_user_ids(self, subject_id: UUID, org_id: UUID) -> list[UUID]:
        url = f"{self.base_url}/internal/subjects/{subject_id}/assigned-user-ids"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    url,
                    params={"org_id": str(org_id)},
                    headers=self.headers,
                )
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Unable to reach exam service. Please try again.")

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Subject not found.")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Unexpected error from exam service.")

        return [UUID(uid) for uid in response.json()]


cbt_service_client = CBTServiceClient()