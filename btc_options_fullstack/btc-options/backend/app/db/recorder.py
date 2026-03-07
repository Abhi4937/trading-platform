"""
Buffers WS ticker ticks in memory and flushes to TimescaleDB every second.
Called synchronously from delta_ws_client._handle_message (no blocking I/O).
"""
import asyncio
import logging
from datetime import datetime, timezone, date

from app.db.connection import get_pool

logger = logging.getLogger(__name__)

_option_buffer: list[tuple] = []
_spot_buffer: list[tuple] = []
_enabled: bool = False


def set_enabled(v: bool) -> None:
    global _enabled
    _enabled = v


def _parse_symbol(symbol: str) -> tuple[str, float, date] | None:
    """Parse C-BTC-68000-080326 → ('call', 68000.0, date(2026,3,8))"""
    parts = symbol.split("-")
    if len(parts) != 4:
        return None
    try:
        opt_type = "call" if parts[0] == "C" else "put"
        strike = float(parts[2])
        d = parts[3]  # DDMMYY
        expiry = date(2000 + int(d[4:6]), int(d[2:4]), int(d[0:2]))
        return opt_type, strike, expiry
    except Exception:
        return None


def record_ticker(symbol: str, data: dict, spot: float) -> None:
    if not _enabled:
        return
    parsed = _parse_symbol(symbol)
    if not parsed:
        return
    opt_type, strike, expiry = parsed
    quotes = data.get("quotes") or {}
    mark_price = float(data.get("mark_price") or 0)
    bid = float(quotes.get("best_bid") or data.get("best_bid") or 0)
    ask = float(quotes.get("best_ask") or data.get("best_ask") or 0)
    raw_iv = quotes.get("mark_iv") or data.get("mark_vol")
    iv = float(raw_iv) if raw_iv else 0.0
    oi = int(data.get("oi_contracts") or 0)
    oi_usd = float(data.get("oi_value_usd") or 0)
    vol = int(data.get("volume") or 0)
    vol_usd = float(data.get("turnover_usd") or 0)

    _option_buffer.append((
        datetime.now(timezone.utc),
        symbol, strike, opt_type, expiry,
        mark_price, bid, ask, iv,
        oi, oi_usd, vol, vol_usd, spot,
    ))


def record_spot(price: float) -> None:
    if not _enabled:
        return
    _spot_buffer.append((datetime.now(timezone.utc), price))


async def start_recorder() -> asyncio.Task:
    return asyncio.create_task(_flush_loop())


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(1.0)
        await _flush()


async def _flush() -> None:
    global _option_buffer, _spot_buffer
    if not _option_buffer and not _spot_buffer:
        return
    pool = get_pool()
    if not pool:
        return

    opt_rows = _option_buffer[:]
    spot_rows = _spot_buffer[:]
    _option_buffer = []
    _spot_buffer = []

    try:
        async with pool.acquire() as conn:
            if opt_rows:
                await conn.executemany(
                    """INSERT INTO option_ticks
                       (time,symbol,strike,option_type,expiry,mark_price,bid,ask,iv,
                        oi_contracts,oi_usd,volume,volume_usd,spot_price)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)""",
                    opt_rows,
                )
            if spot_rows:
                await conn.executemany(
                    "INSERT INTO spot_ticks (time,price) VALUES ($1,$2)",
                    spot_rows,
                )
    except Exception as e:
        logger.error("TimescaleDB flush error: %s", e)
        # Return rows to buffer so they aren't lost
        _option_buffer = opt_rows + _option_buffer
        _spot_buffer = spot_rows + _spot_buffer
