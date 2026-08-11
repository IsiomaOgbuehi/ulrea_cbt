from fastapi import APIRouter, Depends
from item_bank_service.database.database import SessionDep
from item_bank_service.dependencies import verify_internal_secret
from item_bank_service.schemas.schemas import ItemIdsRequest, ItemForScoringRead, ItemForDisplayRead
from item_bank_service.services.item_service import ItemService

router = APIRouter(prefix="/internal/items", tags=["internal"])


''' GET ITEMS FOR SCORING 🔒 '''
@router.post("/for-scoring", response_model=list[ItemForScoringRead])
async def get_items_for_scoring(
    payload: ItemIdsRequest,
    session: SessionDep,
    _: None = Depends(verify_internal_secret),
):
    items = ItemService.get_by_ids_for_scoring(session, payload.item_ids)
    return [ItemForScoringRead.model_validate(i, from_attributes=True) for i in items]


''' GET ITEMS FOR DISPLAY 👁️ '''
@router.post("/for-display", response_model=list[ItemForDisplayRead])
async def get_items_for_display(
    payload: ItemIdsRequest,
    session: SessionDep,
    _: None = Depends(verify_internal_secret),
):
    items = ItemService.get_by_ids_for_display(session, payload.item_ids)
    return [ItemForDisplayRead.model_validate(i, from_attributes=True) for i in items]