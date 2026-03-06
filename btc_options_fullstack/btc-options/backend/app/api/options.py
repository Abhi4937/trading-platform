from datetime import date
from fastapi import APIRouter, HTTPException, Query
from app.models.models import OptionChainResponse
from app.services.option_chain_service import get_option_chain

router = APIRouter()


@router.get("/options", response_model=OptionChainResponse, summary="BTC option chain for a given expiry")
async def option_chain(
    expiry: date = Query(..., description="Expiry date in YYYY-MM-DD format, e.g. 2025-03-28"),
):
    if expiry < date.today():
        raise HTTPException(status_code=400, detail="Expiry date must be in the future")
    try:
        return await get_option_chain(expiry)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Delta Exchange error: {e}")
