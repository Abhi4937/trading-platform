from fastapi import APIRouter, HTTPException
from app.models.models import ExpiryListResponse, SpotResponse
from app.services.option_chain_service import get_expiries, get_spot

router = APIRouter()


@router.get("/expiries", response_model=ExpiryListResponse, summary="List available BTC option expiries")
async def list_expiries():
    try:
        return await get_expiries()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Delta Exchange error: {e}")


@router.get("/spot", response_model=SpotResponse, summary="Current BTC spot price")
async def spot_price():
    try:
        return await get_spot()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
