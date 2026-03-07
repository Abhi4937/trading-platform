# In-memory store for live ticker data from Delta Exchange WebSocket.
# Single process only — no Redis needed.

import time

_tickers: dict[str, dict] = {}
_spot: float = 0.0
_connected: bool = False
_products: list[dict] = []
_expiries: dict = {}          # cached ExpiryListResponse.model_dump()
_expiries_ts: float = 0.0     # timestamp of last fetch


def update_ticker(symbol: str, data: dict) -> None:
    _tickers[symbol] = data


def get_ticker(symbol: str) -> dict:
    return _tickers.get(symbol, {})


def update_spot(price: float) -> None:
    global _spot
    _spot = price


def get_spot() -> float:
    return _spot


def set_connected(v: bool) -> None:
    global _connected
    _connected = v


def is_connected() -> bool:
    return _connected


def has_data() -> bool:
    return bool(_tickers) and _spot > 0


def set_products(products: list[dict]) -> None:
    global _products
    _products = products


def get_products() -> list[dict]:
    return _products


def set_expiries(expiries_dump: dict) -> None:
    global _expiries, _expiries_ts
    _expiries = expiries_dump
    _expiries_ts = time.monotonic()


def get_expiries_cached(ttl: int = 300) -> dict | None:
    """Return cached expiries if fresher than ttl seconds, else None."""
    if _expiries and (time.monotonic() - _expiries_ts) < ttl:
        return _expiries
    return None
