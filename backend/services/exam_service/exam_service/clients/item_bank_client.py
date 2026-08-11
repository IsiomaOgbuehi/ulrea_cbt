# attempt_service/clients/item_bank_client.py

import httpx
from uuid import UUID
from fastapi import HTTPException
from fastapi.logger import logger
from exam_service.core.settings import settings


class ItemBankClient:
    def __init__(self):
        self.base_url = settings.ITEM_BANK_SERVICE_URL
        logger.info("ItemBankClient initialized with base_url=%r", self.base_url)

    async def get_items_for_scoring(self, item_ids: list[UUID]) -> dict[UUID, dict]:
        return await self._post_items("/internal/items/for-scoring", item_ids)

    async def get_items_for_display(self, item_ids: list[UUID]) -> dict[UUID, dict]:
        return await self._post_items("/internal/items/for-display", item_ids)

    async def _post_items(self, path: str, item_ids: list[UUID]) -> dict[UUID, dict]:
        if not item_ids:
            return {}

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    f"{self.base_url}{path}",
                    json={"item_ids": [str(i) for i in item_ids]},
                    headers={"X-Internal-Secret": settings.INTERNAL_SECRET},
                    timeout=5.0,
                )
            except httpx.TimeoutException as e:
                logger.exception("Item bank service timed out calling %s", path)
                raise HTTPException(status_code=502, detail="Item bank service timed out.") from e
            except httpx.ConnectError as e:
                logger.exception("Could not connect to item bank service at %s%s", self.base_url, path)
                raise HTTPException(status_code=502, detail="Could not connect to item bank service.") from e

            if response.status_code != 200:
                logger.error(
                    "Item bank service returned %s for %s: %s",
                    response.status_code, path, response.text[:500],
                )
                raise HTTPException(status_code=502, detail="Could not fetch items from item bank service.")

            items = response.json()
            return {UUID(i["id"]): i for i in items}