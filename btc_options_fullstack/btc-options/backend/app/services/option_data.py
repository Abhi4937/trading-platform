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


def load_spot_series(t_start: int, t_end: int) -> list[dict]:
    """All 1-minute BTC spot marks in [t_start, t_end], sorted ascending.

    Returns [{"time": <unix_secs>, "close": <usdt_per_btc>}].
    One DuckDB read for the full window — avoids per-bar queries in the bar loop.
    """
    conn = get_conn()
    df = conn.execute(
        f"SELECT timestamp_unix AS time, mark_close AS close "
        f"FROM read_parquet('{SPOT_DATA_PATH}') "
        f"WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)} "
        f"  AND mark_close IS NOT NULL "
        f"ORDER BY timestamp_unix ASC"
    ).df()
    if df.empty:
        return []
    out: list[dict] = []
    for _, r in df.iterrows():
        try:
            cf = float(r["close"])
        except (TypeError, ValueError):
            continue
        if cf != cf:  # NaN
            continue
        out.append({"time": int(r["time"]), "close": cf})
    return out


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

    Strike universe is the chain snapshot at `timestamp` (only strikes with a
    valid mark at/before that moment). The filesystem strike index is NOT used
    here because it includes "ghost strikes" — folders that exist on disk but
    whose parquet has no rows at entry_ts. Using them would resolve to strikes
    with mark=0 and cause the trade to skip.
    """
    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    marks_map = get_marks_for_chain(expiry, option_type, timestamp)
    if not marks_map:
        return 0
    sorted_strikes = sorted(marks_map.keys())
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


# ── Batch chain helpers ──────────────────────────────────────────────────────
#
# The per-strike helpers (get_mark_at_or_before, get_oi_at_or_before) open a
# parquet file per call. On Windows Docker bind-mounts each cold open is 5+
# seconds, so scanning a 100-strike chain across 4 legs becomes ~20 min/day.
#
# These helpers do ONE DuckDB glob query per (expiry, option_type, timestamp)
# triple, leaning on hive partitioning to extract `strike` from the path and
# a window function to keep only the latest row per strike. That's a single
# file-system roundtrip; DuckDB parallelises the individual reads internally.

def get_marks_for_chain(
    expiry: str, option_type: Literal["CE", "PE"], timestamp: int,
) -> dict[int, tuple[float, int]]:
    """Return {strike: (mark_close, ts)} for every strike of (expiry, type)
    with mark > 0 at or before `timestamp`. One query for the whole chain.
    """
    glob = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike=*/{option_type}.parquet"
    conn = get_conn()
    sql = (
        f"SELECT strike, mark_close, timestamp_unix "
        f"FROM read_parquet('{glob}', hive_partitioning=true) "
        f"WHERE timestamp_unix <= {int(timestamp)} "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY strike ORDER BY timestamp_unix DESC) = 1"
    )
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        return {}
    out: dict[int, tuple[float, int]] = {}
    for strike, mark, ts in rows:
        if mark is None or ts is None:
            continue
        if float(mark) > 0:
            out[int(strike)] = (float(mark), int(ts))
    return out


def get_chain_snapshot(
    expiry: str, option_type: Literal["CE", "PE"], timestamp: int,
) -> dict[int, dict]:
    """Return {strike: {'mark': float, 'oi': float, 'ts': int}} for the chain.
    Same one-shot batch read as get_marks_for_chain, but pulls oi_close too.
    """
    glob = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike=*/{option_type}.parquet"
    conn = get_conn()
    sql = (
        f"SELECT strike, mark_close, oi_close, timestamp_unix "
        f"FROM read_parquet('{glob}', hive_partitioning=true) "
        f"WHERE timestamp_unix <= {int(timestamp)} "
        f"QUALIFY ROW_NUMBER() OVER (PARTITION BY strike ORDER BY timestamp_unix DESC) = 1"
    )
    try:
        rows = conn.execute(sql).fetchall()
    except Exception:
        return {}
    out: dict[int, dict] = {}
    for strike, mark, oi, ts in rows:
        if mark is None or ts is None:
            continue
        if float(mark) <= 0:
            continue
        import math as _math
        oi_val = 0.0 if oi is None or _math.isnan(float(oi)) else float(oi)
        out[int(strike)] = {"mark": float(mark), "oi": oi_val, "ts": int(ts)}
    return out


def strike_for_closest_premium(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], target_premium: float,
) -> int:
    """Find the strike whose mark price at `timestamp` is closest to `target_premium`."""
    if target_premium <= 0:
        return 0
    marks_map = get_marks_for_chain(expiry, option_type, timestamp)
    if not marks_map:
        return 0
    best_strike = 0
    best_diff = float("inf")
    for k, (mark, _ts) in marks_map.items():
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
    marks_map = get_marks_for_chain(expiry, option_type, timestamp)
    if not marks_map:
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
    for k in sorted(marks_map.keys()):
        mark, _ts = marks_map[k]
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


def strike_for_closest_delta_below(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], target_delta: float,
) -> int:
    """Find the strike with the highest abs(delta) that is still ≤ target_delta.

    If no strike qualifies (all deltas exceed target), falls back to the strike
    with the smallest abs(delta) available.
    """
    from app.core.greeks import implied_vol, compute_greeks

    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    marks_map = get_marks_for_chain(expiry, option_type, timestamp)
    if not marks_map:
        return 0

    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12,
    )
    now_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - now_dt).total_seconds() / (365 * 24 * 3600))

    target = abs(target_delta)
    # Two candidate pools: strikes at-or-below target, and fallback (above target)
    below: list[tuple[float, int]] = []   # (abs_delta, strike)
    above: list[tuple[float, int]] = []

    flag = "call" if option_type == "CE" else "put"
    for k in sorted(marks_map.keys()):
        mark, _ts = marks_map[k]
        iv = implied_vol(mark, spot, k, T, 0.0, flag)
        if not iv or iv <= 0:
            continue
        try:
            g = compute_greeks(spot, k, T, 0.0, iv, flag)
        except Exception:
            continue
        ad = abs(g.delta)
        if ad <= target:
            below.append((ad, int(k)))
        else:
            above.append((ad, int(k)))

    if below:
        # Highest delta still ≤ target (closest from below)
        return max(below, key=lambda x: x[0])[1]
    if above:
        # Nothing qualifies — use smallest delta available
        return min(above, key=lambda x: x[0])[1]
    return 0


def strikes_pool_for_delta_below(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], target_delta: float,
) -> list[tuple[int, float, float]]:
    """Return [(strike, abs_delta, mark)] for all strikes with abs(delta) ≤ target_delta.

    Sorted by abs_delta descending (highest qualifying delta first = closest to target).
    Falls back to all strikes sorted by abs_delta ascending when nothing qualifies.
    Used by the Delta ≤ Match criteria for cross-leg premium alignment.
    """
    from app.core.greeks import implied_vol, compute_greeks

    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return []
    marks_map = get_marks_for_chain(expiry, option_type, timestamp)
    if not marks_map:
        return []

    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12,
    )
    now_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - now_dt).total_seconds() / (365 * 24 * 3600))

    target = abs(target_delta)
    flag = "call" if option_type == "CE" else "put"
    below: list[tuple[int, float, float]] = []   # (strike, abs_delta, mark)
    above: list[tuple[int, float, float]] = []

    for k in sorted(marks_map.keys()):
        mark, _ts = marks_map[k]
        iv = implied_vol(mark, spot, k, T, 0.0, flag)
        if not iv or iv <= 0:
            continue
        try:
            g = compute_greeks(spot, k, T, 0.0, iv, flag)
        except Exception:
            continue
        ad = abs(g.delta)
        if ad <= target:
            below.append((int(k), ad, mark))
        else:
            above.append((int(k), ad, mark))

    if below:
        return sorted(below, key=lambda x: x[1], reverse=True)
    return sorted(above, key=lambda x: x[1])


def strike_for_highest_oi(
    timestamp: int, expiry: str,
    option_type: Literal["CE", "PE"], max_delta: float,
) -> int:
    """OTM strike with highest oi_close, abs(delta) ≤ max_delta. 0 if no candidate.

    OTM filter is strict: CE requires strike > spot, PE requires strike < spot.
    Skips strikes with no mark, no IV, no OI, or delta exceeding the cap.
    """
    from app.core.greeks import implied_vol, compute_greeks

    spot = get_spot_at_or_before(timestamp)
    if spot <= 0:
        return 0
    chain = get_chain_snapshot(expiry, option_type, timestamp)
    if not chain:
        return 0

    expiry_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(
        tzinfo=timezone.utc, hour=12,
    )
    now_dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    T = max(0.0001, (expiry_dt - now_dt).total_seconds() / (365 * 24 * 3600))

    cap = abs(max_delta)
    flag = "call" if option_type == "CE" else "put"
    best_strike = 0
    best_oi = 0.0

    for k in sorted(chain.keys()):
        # OTM filter (strict)
        if option_type == "CE" and k <= spot:
            continue
        if option_type == "PE" and k >= spot:
            continue

        snap = chain[k]
        mark = snap["mark"]
        oi = snap["oi"]
        if oi <= 0:
            continue  # cheap rejection before IV solve
        iv = implied_vol(mark, spot, k, T, 0.0, flag)
        if not iv or iv <= 0:
            continue
        try:
            g = compute_greeks(spot, k, T, 0.0, iv, flag)
        except Exception:
            continue
        if abs(g.delta) > cap:
            continue
        if oi > best_oi:
            best_oi = oi
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


def get_oi_at_or_before(
    expiry: str, strike: int, option_type: Literal["CE", "PE"], timestamp: int,
) -> float:
    """Latest oi_close at or before `timestamp`. Returns 0.0 on miss/NaN."""
    import math as _math
    path = f"{OPTIONS_BASE_DIR}/expiry={expiry}/strike={int(strike)}/{option_type}.parquet"
    if not os.path.exists(path):
        return 0.0
    conn = get_conn()
    res = conn.execute(
        f"SELECT oi_close FROM read_parquet('{path}') "
        f"WHERE timestamp_unix <= {int(timestamp)} "
        f"ORDER BY timestamp_unix DESC LIMIT 1"
    ).fetchone()
    if not res or res[0] is None:
        return 0.0
    try:
        v = float(res[0])
    except Exception:
        return 0.0
    return 0.0 if _math.isnan(v) else v


# ── Realized / Historical Volatility ─────────────────────────────────────────

def hv_at(timestamp: int, window_days: int = 7, bars_per_day: int = 288) -> float:
    """Annualized realized volatility (%) at `timestamp`.

    Computes stdev of log-returns over the last `window_days × bars_per_day`
    spot bars (default 5m bars over 7 days), annualized × sqrt(365 × bars_per_day) × 100.
    Mirrors the RV pane on the historical chart. Returns 0.0 on failure.
    """
    import math as _math
    try:
        import numpy as np
    except Exception:
        return 0.0

    interval_min = max(1, int(round(24 * 60 / bars_per_day)))
    interval = f"{interval_min} minutes"
    rolling_bars = window_days * bars_per_day
    annualize = _math.sqrt(365 * bars_per_day)
    lookback_start = max(0, int(timestamp) - (window_days + 5) * 86400)
    conn = get_conn()
    try:
        q = f"""
        SELECT
            time_bucket(INTERVAL '{interval}', to_timestamp(timestamp_unix)) AS bucket,
            last(mark_close ORDER BY timestamp_unix) AS spot_close
        FROM read_parquet('{SPOT_DATA_PATH}')
        WHERE timestamp_unix >= {lookback_start} AND timestamp_unix <= {int(timestamp)}
        GROUP BY bucket ORDER BY bucket ASC
        """
        df = conn.execute(q).df()
        if df.empty or len(df) < 2:
            return 0.0
        log_ret = np.log(df["spot_close"] / df["spot_close"].shift(1)).dropna()
        recent = log_ret.tail(rolling_bars)
        if len(recent) < 10:
            return 0.0
        rv = float(recent.std() * annualize * 100)
        return round(rv, 2)
    except Exception:
        return 0.0


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
