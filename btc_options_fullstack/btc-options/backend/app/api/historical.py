from datetime import date, datetime, timezone
from fastapi import APIRouter, HTTPException, Query
from app.db.connection import get_pool
from app.core.greeks import compute_greeks, implied_vol
from app.core.config import settings

router = APIRouter()


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except Exception:
        raise HTTPException(400, f"Invalid date: {s}")


def _ts(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(400, f"Invalid timestamp: {s}")


def _check_pool():
    pool = get_pool()
    if not pool:
        raise HTTPException(503, "Historical data unavailable — TimescaleDB not connected")
    return pool


@router.get("/historical/dates")
async def get_historical_dates():
    """Dates that have recorded option data."""
    pool = _check_pool()
    rows = await pool.fetch("""
        SELECT DISTINCT DATE(time AT TIME ZONE 'UTC') AS d
        FROM option_ticks ORDER BY d DESC LIMIT 90
    """)
    return {"dates": [str(r["d"]) for r in rows]}


@router.get("/historical/expiries")
async def get_historical_expiries(date: str = Query(...)):
    """Expiries with data recorded on a given date."""
    pool = _check_pool()
    d = _date(date)
    rows = await pool.fetch("""
        SELECT DISTINCT expiry FROM option_ticks
        WHERE DATE(time AT TIME ZONE 'UTC') = $1
        ORDER BY expiry
    """, d)
    return {"expiries": [str(r["expiry"]) for r in rows]}


@router.get("/historical/times")
async def get_historical_times(
    date: str = Query(...),
    expiry: str = Query(...),
    interval: int = Query(5, description="Bucket size in minutes: 1, 5, or 10"),
):
    """List of bucket timestamps at N-min intervals that have data."""
    pool = _check_pool()
    d = _date(date)
    exp = _date(expiry)
    rows = await pool.fetch("""
        SELECT DISTINCT
            date_trunc('minute', time) -
            (EXTRACT(minute FROM time)::int % $3 * interval '1 minute') AS bucket
        FROM option_ticks
        WHERE DATE(time AT TIME ZONE 'UTC') = $1 AND expiry = $2
        ORDER BY bucket
    """, d, exp, interval)
    return {"timestamps": [r["bucket"].isoformat() for r in rows]}


@router.get("/historical/chain")
async def get_historical_chain(
    expiry: str = Query(...),
    ts: str = Query(..., description="ISO timestamp e.g. 2026-03-07T09:30:00+00:00"),
):
    """Full option chain snapshot reconstructed at a specific historical timestamp."""
    pool = _check_pool()
    exp = _date(expiry)
    target = _ts(ts)

    # Latest tick at-or-before target for each symbol in this expiry
    rows = await pool.fetch("""
        SELECT DISTINCT ON (symbol)
            symbol, strike, option_type, mark_price, bid, ask, iv,
            oi_contracts, oi_usd, volume, volume_usd, spot_price, time
        FROM option_ticks
        WHERE expiry = $1 AND time <= $2
        ORDER BY symbol, time DESC
    """, exp, target)

    if not rows:
        raise HTTPException(404, "No data found for this expiry/timestamp")

    # Best spot price at that moment
    spot_row = await pool.fetchrow(
        "SELECT price FROM spot_ticks WHERE time <= $1 ORDER BY time DESC LIMIT 1", target
    )
    spot = float(spot_row["price"]) if spot_row else float(rows[0]["spot_price"] or 95000.0)

    T = max(0.001, (
        datetime.combine(exp, datetime.min.time())
        .replace(tzinfo=timezone.utc).replace(hour=12) - target
    ).total_seconds() / (365 * 24 * 3600))
    r = settings.RISK_FREE_RATE

    strikes = sorted({float(row["strike"]) for row in rows})
    atm = min(strikes, key=lambda k: abs(k - spot))

    # Index by (strike, opt_type) for fast lookup
    row_map: dict[tuple[float, str], dict] = {}
    for row in rows:
        key = (float(row["strike"]), row["option_type"])
        row_map[key] = dict(row)

    chain = []
    for strike in strikes:
        call_row = row_map.get((strike, "call"))
        put_row = row_map.get((strike, "put"))
        chain.append({
            "strike": strike,
            "is_atm": strike == atm,
            "call": _build_leg(call_row, spot, strike, T, r, "call", exp, atm) if call_row else None,
            "put":  _build_leg(put_row,  spot, strike, T, r, "put",  exp, atm) if put_row  else None,
        })

    atm_call = row_map.get((atm, "call"))
    atm_put  = row_map.get((atm, "put"))
    return {
        "expiry": expiry,
        "snapshot_time": target.isoformat(),
        "spot_price": round(spot, 2),
        "atm_strike": atm,
        "days_to_expiry": round(T * 365, 1),
        "atm_iv_call": round(float(atm_call["iv"] or 0) * 100, 2) if atm_call else 0.0,
        "atm_iv_put":  round(float(atm_put["iv"]  or 0) * 100, 2) if atm_put  else 0.0,
        "chain": chain,
    }


def _build_leg(row: dict, spot: float, strike: float, T: float, r: float,
               opt_type: str, expiry: date, atm: float) -> dict:
    iv = float(row["iv"] or 0)
    mark_price = float(row["mark_price"] or 0)
    if not iv and mark_price > 0:
        iv = implied_vol(mark_price, spot, strike, T, r, opt_type)
    iv = iv if iv > 0 else 0.5
    g = compute_greeks(spot, strike, T, r, iv, opt_type)
    bid = float(row["bid"] or 0)
    ask = float(row["ask"] or 0)
    return {
        "symbol": row["symbol"],
        "strike": strike,
        "option_type": opt_type,
        "expiry": str(expiry),
        "last_price": round(mark_price, 2),
        "bid": round(bid, 2),
        "ask": round(ask, 2),
        "mid": round((bid + ask) / 2 if bid and ask else mark_price, 2),
        "iv": round(iv, 6),
        "iv_pct": round(iv * 100, 2),
        "delta": g.delta,
        "gamma": g.gamma,
        "theta": g.theta,
        "vega": g.vega,
        "rho": g.rho,
        "price_bs": g.price_bs,
        "oi_contracts": int(row["oi_contracts"] or 0),
        "oi_usd": float(row["oi_usd"] or 0),
        "volume": int(row["volume"] or 0),
        "volume_usd": float(row["volume_usd"] or 0),
        "underlying_price": round(spot, 2),
        "days_to_expiry": round(T * 365, 1),
        "is_atm": strike == atm,
    }
