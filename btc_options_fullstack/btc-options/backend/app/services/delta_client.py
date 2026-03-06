import hashlib
import hmac
import time
import logging
from typing import Any
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class DeltaClient:
    def __init__(self):
        self._http = httpx.AsyncClient(
            base_url=settings.DELTA_BASE_URL,
            timeout=12.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    def _auth_headers(self, method: str, path: str) -> dict:
        if not settings.DELTA_API_KEY:
            return {}
        ts = str(int(time.time()))
        msg = method + ts + path
        sig = hmac.new(
            settings.DELTA_API_SECRET.encode(),
            msg.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "api-key": settings.DELTA_API_KEY,
            "timestamp": ts,
            "signature": sig,
            "Content-Type": "application/json",
        }

    async def get(self, path: str, params: dict | None = None) -> Any:
        headers = self._auth_headers("GET", path)
        r = await self._http.get(path, params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def close(self):
        await self._http.aclose()

    # ── Domain helpers ────────────────────────────────────────────────

    async def get_spot_price(self) -> float:
        for sym in ["BTCUSDT", "BTCUSD", "BTC_USDT"]:
            try:
                d = await self.get(f"/v2/tickers/{sym}")
                p = d.get("result", {}).get("close") or d.get("result", {}).get("mark_price")
                if p:
                    return float(p)
            except Exception:
                continue
        logger.warning("Could not fetch BTC spot — using demo price 95000")
        return 95000.0

    async def get_btc_option_products(self) -> list[dict]:
        calls_data = await self.get("/v2/products", params={
            "contract_type": "call_options", "state": "live",
            "underlying_asset_symbol": "BTC", "page_size": 500,
        })
        puts_data = await self.get("/v2/products", params={
            "contract_type": "put_options", "state": "live",
            "underlying_asset_symbol": "BTC", "page_size": 500,
        })
        return (calls_data.get("result") or []) + (puts_data.get("result") or [])

    async def get_ticker(self, symbol: str) -> dict:
        d = await self.get(f"/v2/tickers/{symbol}")
        return d.get("result") or {}

    async def get_candles(self, symbol: str, resolution: str, start_ts: int, end_ts: int) -> list[dict]:
        d = await self.get("/v2/history/candles", params={
            "symbol": symbol, "resolution": resolution,
            "start": start_ts, "end": end_ts,
        })
        return d.get("result") or []


_client: DeltaClient | None = None


def get_delta_client() -> DeltaClient:
    global _client
    if _client is None:
        _client = DeltaClient()
    return _client
