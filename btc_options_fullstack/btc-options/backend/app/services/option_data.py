"""
Option-data helpers used by the multi-day backtest service.

Wraps the DuckDB parquet reads that already power
`backend/app/api/historical.py`. New endpoints reuse these helpers; existing
endpoints can be refactored to call them later (Phase 1 introduces the helpers
without disturbing the existing routes).

All timestamps are UNIX seconds (UTC).
Settlement convention: 12:00 UTC on the expiry date = 17:30 IST.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Literal

# Reuse the existing DuckDB connection + strike index. This avoids spinning up a
# second in-memory DB and keeps the strike-folder scan a one-time cost.
from app.api.historical import (
    SPOT_DATA_PATH,
    get_conn,
    get_strikes_for_expiry,
)
from app.core.greeks import implied_vol

OPTIONS_BASE_DIR = "/home/abhis/btc-data/data/options"

INTERVAL_MAP = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 hour",
}
INTERVAL_SECS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}


# ── Spot ──────────────────────────────────────────────────────────────────────

def get_spot_at(timestamp: int) -> float:
    """BTC spot mark at exactly the given unix timestamp (or 0 if no row)."""
    conn = get_conn()
    res = conn.execute(
        f"SELECT mark_close FROM read_parquet('{SPOT_DATA_PATH}') "
        f"WHERE timestamp_unix = {int(timestamp)}"
    ).fetchone()
    return float(res[0]) if res and res[0] is not None else 0.0


def get_spot_at_or_before(timestamp: int) -> float:
    """Latest spot mark at or before `timestamp` (handles bars that don't align)."""
    conn = get_conn()
    res = conn.execute(
        f"SELECT mark_close FROM read_parquet('{SPOT_DATA_PATH}') "
        f"WHERE timestamp_unix <= {int(timestamp)} "
        f"ORDER BY timestamp_unix DESC LIMIT 1"
    ).fetchone()
    return float(res[0]) if res and res[0] is not None else 0.0


# ── ATM strike resolution ─────────────────────────────────────────────────────

def atm_strike_for(timestamp: int, expiry: str) -> int:
    """Strike in `expiry`'s chain closest to spot at `timestamp`. 0 if no data."""
    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0
    return min(strikes, key=lambda k: abs(k - spot))


def strike_at_offset(timestamp: int, expiry: str, offset: int) -> int:
    """ATM strike + offset (in strike-list positions). offset=-2 → 2 strikes below ATM.

    For SELL (writing) options the convention is:
      - Call: positive offset → OTM (out-of-the-money), negative → ITM
      - Put : positive offset → ITM,  negative → OTM
    Caller adjusts sign based on contract type.
    """
    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0
    sorted_strikes = sorted(strikes)
    atm = min(sorted_strikes, key=lambda k: abs(k - spot))
    idx = sorted_strikes.index(atm) + offset
    if idx < 0 or idx >= len(sorted_strikes):
        return 0
    return sorted_strikes[idx]


def strike_for_strike_type(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], level: str,
) -> int:
    """Resolve AlgoTest StrikeType ('ATM', 'ITM5', 'OTM3') to a concrete strike.

    Convention (matching AlgoTest for index options):
      - For Call:  ITM = strikes < spot,  OTM = strikes > spot
      - For Put :  ITM = strikes > spot,  OTM = strikes < spot
    Depth N counts in strike-list positions away from ATM.
    """
    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0
    sorted_strikes = sorted(strikes)
    atm = min(sorted_strikes, key=lambda k: abs(k - spot))
    atm_idx = sorted_strikes.index(atm)

    if level == "ATM":
        return sorted_strikes[atm_idx]

    import re
    m = re.match(r"^(ITM|OTM)(\d+)$", level)
    if not m:
        return 0
    kind, n_str = m.group(1), int(m.group(2))

    if option_type == "CE":
        # Call ITM = below spot, Call OTM = above spot
        idx = atm_idx + (-n_str if kind == "ITM" else n_str)
    else:
        # Put ITM = above spot, Put OTM = below spot
        idx = atm_idx + (n_str if kind == "ITM" else -n_str)

    if idx < 0 or idx >= len(sorted_strikes):
        return 0
    return sorted_strikes[idx]


def strike_for_closest_premium(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], target_premium: float,
) -> int:
    """Find the strike whose mark price at `timestamp` is closest to `target_premium`."""
    if target_premium <= 0:
        return 0
    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0
    best_strike = 0
    best_diff = float("inf")
    for k in strikes:
        mark, _ = get_mark_at_or_before(expiry, k, option_type, timestamp)
        if mark <= 0:
            continue
        d = abs(mark - target_premium)
        if d < best_diff:
            best_diff = d
            best_strike = int(k)
    return best_strike


def strike_for_closest_delta(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], target_delta: float,
) -> int:
    """Find the strike whose Black-Scholes delta at `timestamp` is closest to `target_delta`.

    For calls target_delta is positive (e.g. 0.30), for puts negative (e.g. -0.30) — the
    function takes the absolute distance, so callers can pass either sign.
    """
    from app.core.greeks import implied_vol, compute_greeks

    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0

    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12,
    )
    now_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - now_dt).total_seconds() / (365 * 24 * 3600))

    target = abs(target_delta)
    best_strike = 0
    best_diff = float("inf")
    flag = "call" if option_type == "CE" else "put"
    for k in sorted(strikes):
        mark, _ = get_mark_at_or_before(expiry, k, option_type, timestamp)
        if mark <= 0:
            continue
        iv = implied_vol(mark, spot, k, T, 0.0, flag)
        if not iv or iv <= 0:
            continue
        try:
            g = compute_greeks(spot, k, T, 0.0, iv, flag)
        except Exception:
            continue
        d = abs(abs(g.delta) - target)
        if d < best_diff:
            best_diff = d
            best_strike = int(k)
    return best_strike


# ── Per-leg bar series ────────────────────────────────────────────────────────

def load_leg_series(
    expiry: str,
    strike: int,
    option_type: Literal["CE", "PE"],
    t_start: int,
    t_end: int,
    timeframe: str = "1m",
) -> list[dict]:
    """Bucketed (ts, mark_close) pairs for one option leg.

    Returns: [{"time": <unix_secs>, "close": <usdt_per_btc>}]
    """
    interval = INTERVAL_MAP.get(timeframe, "1 minute")
    path = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike={int(strike)}/{option_type}.parquet"
    if not os.path.exists(path):
        return []

    conn = get_conn()
    df = conn.execute(f"""
        SELECT
            time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
            last(mark_close ORDER BY timestamp_unix) AS close
        FROM read_parquet('{path}')
        WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
          AND mark_close IS NOT NULL
        GROUP BY bucket ORDER BY bucket ASC
    """).df()
    if df.empty:
        return []
    df["time"] = df["bucket"].apply(lambda x: int(x.timestamp()))
    out: list[dict] = []
    for _, r in df.iterrows():
        c = r["close"]
        # DuckDB can still return NaN for buckets where every input row was NaN;
        # filter again in Python so the simulator never sees one.
        if c is None:
            continue
        try:
            cf = float(c)
        except (TypeError, ValueError):
            continue
        if cf != cf:        # NaN check
            continue
        out.append({"time": int(r["time"]), "close": cf})
    return out


def get_mark_at(
    expiry: str, strike: int, option_type: Literal["CE", "PE"], timestamp: int,
) -> float:
    """Single mark_close at exact `timestamp`. Returns 0 if no row."""
    path = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike={int(strike)}/{option_type}.parquet"
    if not os.path.exists(path):
        return 0.0
    conn = get_conn()
    res = conn.execute(
        f"SELECT mark_close FROM read_parquet('{path}') "
        f"WHERE timestamp_unix = {int(timestamp)}"
    ).fetchone()
    return float(res[0]) if res and res[0] is not None else 0.0


def get_mark_at_or_before(
    expiry: str, strike: int, option_type: Literal["CE", "PE"], timestamp: int,
) -> tuple[float, int]:
    """Latest mark at or before `timestamp`. Returns (mark, actual_ts) or (0, 0)."""
    path = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike={int(strike)}/{option_type}.parquet"
    if not os.path.exists(path):
        return 0.0, 0
    conn = get_conn()
    res = conn.execute(
        f"SELECT mark_close, timestamp_unix FROM read_parquet('{path}') "
        f"WHERE timestamp_unix <= {int(timestamp)} "
        f"ORDER BY timestamp_unix DESC LIMIT 1"
    ).fetchone()
    if not res or res[0] is None:
        return 0.0, 0
    return float(res[0]), int(res[1])


# ── ATM IV ────────────────────────────────────────────────────────────────────

def atm_iv_at(timestamp: int, expiry: str, fallback_search: int = 4) -> float:
    """ATM IV (decimal, e.g. 0.55 = 55%) at `timestamp` for `expiry`.

    Computes via Black-Scholes implied_vol from the average of CE+PE marks at
    the ATM strike. Falls back to ±N strikes if ATM has no marks.
    """
    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0.0

    strikes = get_strikes_for_expiry(expiry)
    if not strikes:
        return 0.0

    # Time to expiry (years). Settlement = 12:00 UTC on expiry date.
    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12,
    )
    now_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - now_dt).total_seconds() / (365 * 24 * 3600))

    sorted_strikes = sorted(strikes)
    atm = min(sorted_strikes, key=lambda k: abs(k - spot))
    atm_idx = sorted_strikes.index(atm)

    # Try ATM, then walk outward up to `fallback_search` positions on each side.
    candidates = [atm]
    for d in range(1, fallback_search + 1):
        if atm_idx - d >= 0:
            candidates.append(sorted_strikes[atm_idx - d])
        if atm_idx + d < len(sorted_strikes):
            candidates.append(sorted_strikes[atm_idx + d])

    for K in candidates:
        ce = get_mark_at(expiry, K, "CE", timestamp)
        pe = get_mark_at(expiry, K, "PE", timestamp)
        ivs = []
        if ce > 0:
            v = implied_vol(ce, spot, K, T, 0.0, "call")
            if v and v > 0:
                ivs.append(v)
        if pe > 0:
            v = implied_vol(pe, spot, K, T, 0.0, "put")
            if v and v > 0:
                ivs.append(v)
        if ivs:
            return sum(ivs) / len(ivs)
    return 0.0


# ── Expiry resolution (Python port of frontend generateExpiries) ──────────────

ExpirySelector = Literal[
    "current", "next", "next_to_next", "weekly", "next_weekly",
    "monthly", "next_monthly", "current_plus_n",
]


def _last_friday_of_month(year: int, month: int) -> date:
    """Last Friday of given (year, month). month is 1-12."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != 4:  # Friday=4
        d -= timedelta(days=1)
    return d


def list_expiries_for(date_iso: str, time_ist: str = "09:20") -> list[dict]:
    """Mirrors `generateExpiries` in HistoricalDashboard.tsx — produces
    [{"date": "YYYY-MM-DD", "label": "Current Friday (...)"}, ...]
    sorted ascending. After 17:30 IST, today's contract has expired and base
    advances to the next calendar day.
    """
    base = date.fromisoformat(date_iso)
    h, m = (int(x) for x in time_ist.split(":"))
    if h * 60 + m >= 17 * 60 + 30:
        base = base + timedelta(days=1)

    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]

    monthly = _last_friday_of_month(base.year, base.month)
    if monthly <= base:
        next_month = base.month + 1
        next_year = base.year
        if next_month > 12:
            next_month -= 12
            next_year += 1
        monthly = _last_friday_of_month(next_year, next_month)
    nm_month = monthly.month + 1
    nm_year = monthly.year
    if nm_month > 12:
        nm_month -= 12
        nm_year += 1
    next_monthly = _last_friday_of_month(nm_year, nm_month)

    this_week_friday = base
    while this_week_friday.weekday() != 4:
        this_week_friday += timedelta(days=1)

    def _label(d: date, fallback: str) -> str:
        if d == monthly:
            return "Monthly"
        if d.weekday() == 4:
            return "Weekly"
        return fallback

    added: dict[str, str] = {}

    def add(d: date, label: str) -> None:
        s = d.isoformat()
        if s not in added:
            added[s] = f"{label} ({s})"

    add(base, _label(base, f"Current {DAYS[base.weekday()]}"))
    d1 = base + timedelta(days=1)
    add(d1, _label(d1, f"Next {DAYS[d1.weekday()]}"))
    d2 = base + timedelta(days=2)
    add(d2, _label(d2, f"Next-to-Next {DAYS[d2.weekday()]}"))

    add(this_week_friday, "Monthly" if this_week_friday == monthly else "Weekly")
    nw = this_week_friday + timedelta(days=7)
    add(nw, "Monthly" if nw == monthly else "Next Weekly")
    nnw = nw + timedelta(days=7)
    add(nnw, "Monthly" if nnw == monthly else "Next-to-Next Weekly")

    add(monthly, "Monthly")
    add(next_monthly, "Next Monthly")

    return [{"date": d, "label": lbl} for d, lbl in sorted(added.items())]


def resolve_expiry(
    date_iso: str,
    time_ist: str,
    selector: ExpirySelector,
    offset: int = 0,
) -> str | None:
    """Pick a single expiry date string by selector. Returns None if no match.

    Selector values:
      current       — the chronologically first expiry from base
      next          — base + 1 calendar day
      next_to_next  — base + 2 calendar days
      weekly        — first Friday on/after base
      next_weekly   — weekly + 7 days
      monthly       — last Friday of base's month (or next month if past)
      next_monthly  — month after monthly
      current_plus_n — current + offset positions in the expiry list
    """
    expiries = list_expiries_for(date_iso, time_ist)
    if not expiries:
        return None

    # Position-based selectors: independent of label text, anchored to the
    # chronologically-sorted list.
    if selector == "current":
        return expiries[0]["date"]
    if selector == "next":
        return expiries[1]["date"] if len(expiries) > 1 else None
    if selector == "next_to_next":
        return expiries[2]["date"] if len(expiries) > 2 else None
    if selector == "current_plus_n":
        return expiries[offset]["date"] if 0 <= offset < len(expiries) else None

    # Date-shape selectors — derive directly from the dates so the upcoming
    # Friday is "weekly" even when it's also the monthly (matches AlgoTest's
    # "Weekly" semantic: next Friday expiry, regardless of label).
    fridays  = [e["date"] for e in expiries
                if date.fromisoformat(e["date"]).weekday() == 4]
    monthlys = [e["date"] for e in expiries
                if date.fromisoformat(e["date"])
                == _last_friday_of_month(
                    date.fromisoformat(e["date"]).year,
                    date.fromisoformat(e["date"]).month,
                )]

    if selector == "weekly":
        return fridays[0] if fridays else None
    if selector == "next_weekly":
        return fridays[1] if len(fridays) > 1 else None
    if selector == "monthly":
        return monthlys[0] if monthlys else None
    if selector == "next_monthly":
        return monthlys[1] if len(monthlys) > 1 else None
    return None
