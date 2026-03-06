import math
import random
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.cache import redis_cache
from app.core.config import settings
from app.core.greeks import compute_greeks
from app.models.models import (
    CandleBar, IVRVPoint, IVRVResponse, IVSmilePoint,
    IVSmileResponse, PremiumChartResponse,
)
from app.services.delta_client import get_delta_client
from app.services.option_chain_service import get_option_chain

router = APIRouter()

TF_TO_RESOLUTION = {"1m": "1m","5m": "5m","15m": "15m","1h": "1h","4h": "4h","1d": "1d"}


@router.get("/plot-data/premium", response_model=PremiumChartResponse,
            summary="OHLCV candle data for an option contract")
async def premium_chart(
    expiry: date = Query(...),
    strike: float = Query(...),
    option_type: Literal["call","put"] = Query("call"),
    timeframe: str = Query("1h", regex="^(1m|5m|15m|1h|4h|1d)$"),
):
    cache_key = f"candles:{expiry}:{strike}:{option_type}:{timeframe}"
    cached = await redis_cache.get(cache_key)
    if cached:
        return PremiumChartResponse(**cached)

    # Resolve symbol
    chain = await get_option_chain(expiry)
    row = next((r for r in chain.chain if r.strike == strike), None)
    leg = row.call if option_type == "call" else row.put if row else None
    symbol = leg.symbol if leg else ""

    candles = []
    if symbol and settings.DELTA_API_KEY:
        try:
            now_ts = int(datetime.now(timezone.utc).timestamp())
            start_ts = now_ts - _tf_seconds(timeframe) * 200
            client = get_delta_client()
            raw = await client.get_candles(symbol, TF_TO_RESOLUTION[timeframe], start_ts, now_ts)
            for c in raw:
                candles.append(CandleBar(
                    timestamp=datetime.fromtimestamp(c["time"], tz=timezone.utc),
                    open=float(c["open"]), high=float(c["high"]),
                    low=float(c["low"]),  close=float(c["close"]),
                    volume=float(c.get("volume", 0)),
                ))
        except Exception:
            candles = _demo_candles(leg.last_price if leg else 2000, timeframe)
    else:
        candles = _demo_candles(leg.last_price if leg else 2000, timeframe)

    resp = PremiumChartResponse(
        symbol=symbol or f"BTC-DEMO-{strike}-{option_type[0].upper()}",
        strike=strike, option_type=option_type,
        expiry=expiry, timeframe=timeframe, candles=candles,
    )
    await redis_cache.set(cache_key, resp.model_dump(), settings.CACHE_TTL_CANDLES)
    return resp


@router.get("/plot-data/iv-smile", response_model=IVSmileResponse,
            summary="IV smile across all strikes for an expiry")
async def iv_smile(expiry: date = Query(...)):
    chain = await get_option_chain(expiry)
    points = []
    for row in chain.chain:
        if not row.call or not row.put:
            continue
        moneyness = math.log(row.strike / chain.spot_price) if chain.spot_price > 0 else 0
        points.append(IVSmilePoint(
            strike=row.strike,
            call_iv=row.call.iv_pct,
            put_iv=row.put.iv_pct,
            delta=row.call.delta,
            moneyness=round(moneyness, 4),
        ))
    atm_iv = next((p.call_iv for p in points if abs(p.moneyness) < 0.02), 0)
    return IVSmileResponse(
        expiry=expiry, spot_price=chain.spot_price,
        atm_strike=chain.atm_strike, atm_iv=atm_iv, points=points,
    )


@router.get("/plot-data/iv-rv", response_model=IVRVResponse,
            summary="Implied vol vs Realised vol (ATM, historical series)")
async def iv_vs_rv(
    expiry: date = Query(...),
    window: int  = Query(30, ge=7, le=90, description="Lookback days for RV"),
):
    chain = await get_option_chain(expiry)
    atm_iv = (chain.atm_iv_call + chain.atm_iv_put) / 2

    # In production: fetch historical ATM IV from Parquet/TimescaleDB.
    # Here we simulate a plausible series with mean-reversion + noise.
    series = []
    iv_level = atm_iv
    rv_level = atm_iv * 0.88
    for i in range(window, -1, -1):
        d = date.today() - timedelta(days=i)
        iv_level += random.gauss(0, 2) + (atm_iv - iv_level) * 0.1
        iv_level = max(10.0, iv_level)
        rv_level += random.gauss(0, 1.5) + (atm_iv * 0.85 - rv_level) * 0.08
        rv_level = max(8.0, rv_level)
        series.append(IVRVPoint(
            label=d.strftime("%d %b"),
            implied_vol=round(iv_level, 2),
            realised_vol=round(rv_level, 2),
        ))

    return IVRVResponse(
        expiry=expiry, window_days=window,
        atm_strike=chain.atm_strike, series=series,
    )


def _tf_seconds(tf: str) -> int:
    return {"1m":60,"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}.get(tf, 3600)


def _demo_candles(base_price: float, timeframe: str) -> list[CandleBar]:
    now = datetime.now(timezone.utc)
    bars = []
    price = base_price or 2000
    for i in range(100, 0, -1):
        ts = now - timedelta(seconds=_tf_seconds(timeframe) * i)
        price += random.gauss(0, price * 0.01)
        price = max(1, price)
        hi = price * (1 + abs(random.gauss(0, 0.005)))
        lo = price * (1 - abs(random.gauss(0, 0.005)))
        bars.append(CandleBar(
            timestamp=ts, open=round(price, 2), high=round(hi, 2),
            low=round(lo, 2), close=round(price, 2),
            volume=round(random.uniform(10, 500), 2),
        ))
    return bars
