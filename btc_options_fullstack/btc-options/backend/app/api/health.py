from datetime import datetime, timezone
from fastapi import APIRouter

from app.services import ticker_store
from app.services.delta_client import get_delta_client

router = APIRouter()


@router.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/debug/ticker/{symbol}")
async def debug_ticker(symbol: str):
    return {"symbol": symbol, "data": ticker_store.get_ticker(symbol)}


@router.get("/debug/compare/{symbol}")
async def debug_compare(symbol: str):
    store_data = ticker_store.get_ticker(symbol)
    try:
        client = get_delta_client()
        rest_data = await client.get_ticker(symbol)
    except Exception as e:
        rest_data = {"error": str(e)}

    def extract(data: dict) -> dict:
        if not data:
            return {}
        quotes = data.get("quotes") or {}
        return {
            "mark_price": data.get("mark_price"),
            "close":      data.get("close"),
            "bid":        quotes.get("best_bid") or data.get("best_bid"),
            "ask":        quotes.get("best_ask") or data.get("best_ask"),
            "mark_iv":    quotes.get("mark_iv") or data.get("mark_vol"),
            "volume":     data.get("volume"),
            "oi":         data.get("oi_contracts") or data.get("oi"),
            "timestamp":  data.get("time") or data.get("timestamp"),
        }

    return {
        "symbol": symbol,
        "ws_store":  extract(store_data),
        "rest_live": extract(rest_data),
        "raw_store": store_data,
        "raw_rest":  rest_data,
    }
