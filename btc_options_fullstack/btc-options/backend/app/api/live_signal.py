"""LiveSignal API — exposes scan_live_candidates to the frontend.

Single endpoint: GET /api/v1/live-signal/scan
Returns the ranked grid of (expiry × delta) strangle candidates with full
analytics + hard-filter flags + v2 calibration fields.

Response is cached for 5 seconds at the module level so multiple polling
clients don't each trigger a fresh chain-build / analytics pass — a single
scan typically runs ~1s and the polling cadence is ~7s, so a 5s TTL keeps
data fresh but de-duplicates concurrent requests.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services import live_signal_compute as lsc

router = APIRouter()
log = logging.getLogger(__name__)

_CACHE_TTL_S: float = 5.0
_cache_lock = asyncio.Lock()
_cache_ts: float = 0.0
_cache_payload: Optional[dict] = None


async def _scan_cached() -> dict:
    """Return last scan if within TTL; else recompute under a lock."""
    global _cache_ts, _cache_payload
    now = time.time()
    if _cache_payload is not None and (now - _cache_ts) < _CACHE_TTL_S:
        return _cache_payload

    async with _cache_lock:
        # Re-check inside the lock — another concurrent caller may have
        # populated the cache while we waited.
        now = time.time()
        if _cache_payload is not None and (now - _cache_ts) < _CACHE_TTL_S:
            return _cache_payload

        candidates = await lsc.scan_live_candidates()
        payload = {
            "ts": int(now),
            "count": len(candidates),
            "candidates": [lsc.candidate_to_dict(c) for c in candidates],
        }
        _cache_ts = now
        _cache_payload = payload
        return payload


@router.get("/scan", summary="Rank all live (expiry × Δ) strangle candidates")
async def scan(
    top_n: Optional[int] = Query(None, ge=1, le=200,
                                 description="Optional cap on returned rows."),
    only_passing: bool = Query(False,
                               description="If true, drop candidates where flt_all_pass is False."),
):
    """Return the ranked list of strangle candidates from live ws data.

    Pipeline (cached for 5s):
      1. Snapshot ticker_store + ATM IV from M3 row at now()
      2. For each live expiry × 6 target deltas: pick CE+PE at closest delta,
         build SELL strangle (qty=100), compute analytics + v2 calibration
         lookup
      3. Compute hard-filter flags (IVP>50, IV-RV>0, ADX<30, DTE 5–14, GEX OK)
      4. Sort by quality_score desc

    Frontend polls this every 7s.
    """
    try:
        payload = await _scan_cached()
    except Exception as e:
        log.exception("scan_live_candidates failed: %s", e)
        raise HTTPException(status_code=503, detail=f"live signal scan failed: {e}")

    candidates = payload["candidates"]
    if only_passing:
        candidates = [c for c in candidates if c.get("flt_all_pass")]
    if top_n is not None:
        candidates = candidates[:top_n]
    return {
        "ts": payload["ts"],
        "count": len(candidates),
        "total_scanned": payload["count"],
        "candidates": candidates,
    }
