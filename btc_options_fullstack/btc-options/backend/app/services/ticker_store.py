# In-memory store for live ticker data from Delta Exchange WebSocket.
# Single process only — no Redis needed.

_tickers: dict[str, dict] = {}
_spot: float = 0.0
_connected: bool = False


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
