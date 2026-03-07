"""
Historical backfill: given a date, reconstruct the option chain as it existed
then — get spot from Delta candles, build expiry/strike universe, fetch candles
for each symbol, store in TimescaleDB.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.db.connection import get_pool
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

DELTA_BASE = "https://api.india.delta.exchange"
RESOLUTIONS = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

# Global progress tracker (single-worker only)
_progress: dict = {"running": False, "done": 0, "total": 0, "status": "idle", "errors": 0}


class BackfillRequest(BaseModel):
    date: str          # ISO date: "2025-12-01"
    resolution: str = "1h"   # 1m 5m 15m 30m 1h 4h 1d
    strike_count: int = 20   # ±N strikes around ATM
    strike_interval: int = 200  # $200 between strikes


@router.get("/historical/backfill/status")
async def backfill_status():
    return _progress


@router.post("/historical/backfill")
async def start_backfill(req: BackfillRequest, background_tasks: BackgroundTasks):
    if _progress["running"]:
        raise HTTPException(409, "Backfill already running")
    if req.resolution not in RESOLUTIONS:
        raise HTTPException(400, f"resolution must be one of {RESOLUTIONS}")
    try:
        from_date = date.fromisoformat(req.date)
    except ValueError:
        raise HTTPException(400, "Invalid date format, use YYYY-MM-DD")

    pool = get_pool()
    if not pool:
        raise HTTPException(503, "TimescaleDB not connected")

    background_tasks.add_task(
        _run_backfill, from_date, req.resolution, req.strike_count, req.strike_interval
    )
    return {"status": "started", "date": req.date, "resolution": req.resolution}


async def _run_backfill(from_date: date, resolution: str, strike_count: int, strike_interval: int):
    global _progress
    _progress = {"running": True, "done": 0, "total": 0, "status": "Fetching spot price...", "errors": 0}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Get BTC spot at midnight of from_date
            spot = await _get_spot_at(client, from_date)
            if not spot:
                _progress["status"] = "ERROR: Could not fetch spot price"
                return
            logger.info("Backfill: date=%s spot=%.0f", from_date, spot)

            # 2. Store spot candles for the whole range
            _progress["status"] = f"Spot at {from_date}: ${spot:,.0f} — storing spot history..."
            await _store_spot_candles(client, from_date, resolution)

            # 3. Generate expiry dates
            expiries = _generate_expiries(from_date)
            logger.info("Backfill: expiries=%s", expiries)

            # 4. Generate ATM + ±N strikes
            atm = round(spot / strike_interval) * strike_interval
            strikes = [atm + i * strike_interval for i in range(-strike_count, strike_count + 1)]

            # 5. Build full symbol list (call + put for each strike × expiry)
            symbols: list[tuple[str, int, str, date]] = []  # (symbol, strike, opt_type, expiry)
            for exp in expiries:
                ddmmyy = exp.strftime("%d%m%y")
                for strike in strikes:
                    for prefix, opt_type in [("C", "call"), ("P", "put")]:
                        sym = f"{prefix}-BTC-{int(strike)}-{ddmmyy}"
                        symbols.append((sym, strike, opt_type, exp))

            _progress["total"] = len(symbols)
            _progress["status"] = f"Fetching {len(symbols)} symbols ({len(expiries)} expiries × {len(strikes)} strikes × 2)..."
            logger.info("Backfill: %d symbols to fetch", len(symbols))

            # 6. Fetch candles for each symbol (rate-limit: ~50 req/min)
            sem = asyncio.Semaphore(5)  # max 5 concurrent requests
            tasks = [
                _fetch_and_store(client, sem, sym, strike, opt_type, exp, from_date, resolution, spot)
                for sym, strike, opt_type, exp in symbols
            ]
            await asyncio.gather(*tasks)

        _progress["status"] = f"Done — {_progress['done']} symbols stored, {_progress['errors']} empty/errors"
        logger.info("Backfill complete: %s", _progress["status"])
    except Exception as e:
        logger.error("Backfill error: %s", e, exc_info=True)
        _progress["status"] = f"ERROR: {e}"
    finally:
        _progress["running"] = False


async def _fetch_and_store(client, sem, symbol, strike, opt_type, exp, from_date, resolution, spot):
    global _progress
    async with sem:
        try:
            start_ts = int(datetime.combine(from_date, datetime.min.time(), timezone.utc).timestamp())
            end_ts = int(datetime.combine(exp, datetime.min.time(), timezone.utc).replace(hour=13).timestamp())
            url = f"{DELTA_BASE}/v2/history/candles?symbol={symbol}&resolution={resolution}&start={start_ts}&end={end_ts}"
            r = await client.get(url)
            data = r.json()
            candles = data.get("result", [])

            if candles:
                rows = []
                for c in candles:
                    ts = datetime.fromtimestamp(c["time"], tz=timezone.utc)
                    mark_price = float(c["close"] or 0)
                    if mark_price <= 0:
                        continue
                    bid = round(mark_price * 0.995, 2)
                    ask = round(mark_price * 1.005, 2)
                    vol = float(c.get("volume") or 0)
                    rows.append((
                        ts, symbol, float(strike), opt_type, exp,
                        mark_price, bid, ask,
                        0.0,   # iv — computed on read
                        0, 0.0, int(vol), vol * mark_price * 0.001, spot,
                    ))

                if rows:
                    pool = get_pool()
                    async with pool.acquire() as conn:
                        # Upsert: skip duplicates by time+symbol
                        await conn.executemany("""
                            INSERT INTO option_ticks
                            (time,symbol,strike,option_type,expiry,mark_price,bid,ask,iv,
                             oi_contracts,oi_usd,volume,volume_usd,spot_price)
                            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                            ON CONFLICT DO NOTHING
                        """, rows)
            _progress["done"] += 1
        except Exception as e:
            _progress["errors"] += 1
            logger.debug("Backfill fetch error %s: %s", symbol, e)
        await asyncio.sleep(0.05)  # gentle rate limiting


async def _store_spot_candles(client, from_date: date, resolution: str):
    """Store BTCUSD historical candles into spot_ticks for the month around from_date."""
    start_ts = int(datetime.combine(from_date, datetime.min.time(), timezone.utc).timestamp())
    end_ts = int((datetime.combine(from_date, datetime.min.time(), timezone.utc) + timedelta(days=60)).timestamp())
    url = f"{DELTA_BASE}/v2/history/candles?symbol=BTCUSD&resolution={resolution}&start={start_ts}&end={end_ts}"
    r = await client.get(url)
    candles = r.json().get("result", [])
    if not candles:
        return
    rows = [(datetime.fromtimestamp(c["time"], tz=timezone.utc), float(c["close"])) for c in candles if c.get("close")]
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO spot_ticks (time,price) VALUES ($1,$2) ON CONFLICT DO NOTHING",
            rows
        )
    logger.info("Backfill: stored %d spot candles", len(rows))


async def _get_spot_at(client, d: date) -> Optional[float]:
    """Get BTCUSD close price at midnight of given date."""
    ts = int(datetime.combine(d, datetime.min.time(), timezone.utc).timestamp())
    url = f"{DELTA_BASE}/v2/history/candles?symbol=BTCUSD&resolution=1h&start={ts}&end={ts + 7200}"
    r = await client.get(url)
    candles = r.json().get("result", [])
    if candles:
        return float(candles[-1]["close"])
    return None


def _generate_expiries(from_date: date) -> list[date]:
    """Generate expiry dates as Delta would list them from a given 'current' date."""
    expiries = set()

    # Daily: next 3 days
    for i in range(1, 4):
        expiries.add(from_date + timedelta(days=i))

    # Weekly: next 4 Fridays
    days_to_friday = (4 - from_date.weekday()) % 7
    if days_to_friday == 0:
        days_to_friday = 7
    friday = from_date + timedelta(days=days_to_friday)
    for i in range(4):
        expiries.add(friday + timedelta(weeks=i))

    # Monthly: last Friday of each of the next 3 months
    for month_offset in range(1, 4):
        m = from_date.month + month_offset
        y = from_date.year + (m - 1) // 12
        m = ((m - 1) % 12) + 1
        # Find last Friday of month
        last_day = date(y, m, 28)
        while True:
            next_m = last_day + timedelta(days=1)
            if next_m.month != m:
                break
            last_day = next_m
        while last_day.weekday() != 4:
            last_day -= timedelta(days=1)
        expiries.add(last_day)

    return sorted(expiries)
