import asyncio
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

    def _auth_headers(self, method: str, path: str, params: dict | None = None) -> dict:
        if not settings.DELTA_API_KEY:
            return {}
        ts = str(int(time.time()))
        query_string = ""
        if params:
            query_string = "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        msg = method + ts + path + query_string
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
        headers = self._auth_headers("GET", path, params)
        start = time.perf_counter()
        try:
            r = await self._http.get(path, params=params, headers=headers)
            duration_ms = (time.perf_counter() - start) * 1000
            r.raise_for_status()
            logger.info(
                "DELTA  GET  %s  params=%s  status=%s  %.1fms",
                path, params or {}, r.status_code, duration_ms,
            )
            return r.json()
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "DELTA  GET  %s  params=%s  ERROR=%s  %.1fms",
                path, params or {}, e, duration_ms,
            )
            raise

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

    async def _get_all_pages(self, contract_type: str) -> list[dict]:
        all_results = []
        page = 1
        max_pages = 20  # Safety limit: 20,000 products per type
        while page <= max_pages:
            data = await self.get("/v2/products", params={
                "contract_type": contract_type, "state": "live",
                "underlying_asset_symbol": "BTC", "page_size": 1000,
                "page_number": page
            })
            result = data.get("result") or []
            if not result:
                break
            
            # If we get the same number of results as before, or no new results, break
            # (Some APIs return the last page indefinitely)
            all_results.extend(result)
            
            if len(result) < 1000:
                break
            page += 1
        
        logger.info("Fetched %d products for %s across %d pages", len(all_results), contract_type, page if page <= max_pages else max_pages)
        return all_results

    async def get_btc_option_products(self) -> list[dict]:
        calls_task = asyncio.create_task(self._get_all_pages("call_options"))
        puts_task = asyncio.create_task(self._get_all_pages("put_options"))
        
        all_products = (await calls_task) + (await puts_task)
        
        return [
            p for p in all_products
            if (p.get("underlying_asset", {}) or {}).get("symbol", "").upper() == "BTC"
            or str(p.get("symbol", "")).upper().startswith("BTC")
        ]

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
