"""
M-Month — monthly + bimonthly + last-Fri-rolling strangle sweep backtester.

Sibling to m7_batch_backtester.py. Same path-record-everything-replay-nothing
contract: simulate once, query forever. Three trade cycles share the same
infrastructure but differ in their entry anchor, exit anchor, and expiry
selector:

  Monthly         : enter first-Mon 23:00 IST  → exit same-month last-Fri 11:00 IST
                    sells same-month last-Friday expiry  (~28 DTE at entry, ~0 at exit)
  Bimonthly       : enter first-Mon 23:00 IST  → exit same-month last-Fri 11:00 IST
                    sells next-month last-Friday expiry (~58 DTE at entry, ~30 at exit)
  Last-Fri rolling: enter last-Fri  10:00 IST  → exit next-month last-Fri 11:00 IST
                    sells next-month last-Friday expiry (~28 DTE at entry, ~0 at exit)

Strike-selection / cost / margin / greeks code is re-imported from
m7_batch_backtester to avoid duplication.

Outputs (under /home/abhis/btc-data/derived/m_month/):
  m_month_trades.parquet
  m_month_paths/entry_month=YYYY-MM/part.parquet

Run:
  python -m app.analytics.m_month_batch_backtester                     # full
  python -m app.analytics.m_month_batch_backtester --since 2024-01-01
  python -m app.analytics.m_month_batch_backtester --max-months 1 --cycles monthly
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import math
import os
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import duckdb
import numpy as np
import pandas as pd

from app.analytics.calibration_builder import (
    DTE_BUCKETS,
    IVP_BUCKETS,
    M3_CONTEXT_COLS,
    _delta_label,
    _label_for_range,
    _spot_label,
)
from app.analytics.enrich_options import (
    DERIVED_DIR,
    expiry_dt_unix,
    list_expiries,
    load_chain_for_expiry,
)
from app.analytics.m7_batch_backtester import (
    DEFAULT_COST_CFG,
    IV_BANDS,
    QTY_LOTS,
    SPOT_PATH,
    _entry_cost_breakdown,
    _ff_lookup,
    _load_m3,
    _m3_at_or_before,
    compute_atm_iv_series,
    compute_entry_margin,
    iv_band_label,
    load_leg_bars_1m,
    load_spot_window,
    pick_strikes,
)
from app.core.greeks import compute_greeks, implied_vol
from app.services.costs import CONTRACT_VALUE

# ── Paths & constants ────────────────────────────────────────────────────────

M_MONTH_OUT_DIR = os.path.join(DERIVED_DIR, "m_month")
TRADES_OUT = os.path.join(M_MONTH_OUT_DIR, "m_month_trades.parquet")
PATHS_OUT_DIR = os.path.join(M_MONTH_OUT_DIR, "m_month_paths")

TARGET_DELTAS = (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50)

# Stage 1: single anchor per cycle. Stage 2 expands these lists.
# Four cycles total:
#   monthly            = first-Mon entry, sells current-month last-Fri expiry, exits same-month last-Fri
#   bimonthly          = first-Mon entry, sells next-month last-Fri expiry, exits same-month last-Fri
#   lastfri_monthly    = last-Fri entry, sells next-month last-Fri expiry (= exit day, ~0 DTE at exit)
#   lastfri_bimonthly  = last-Fri entry, sells month-after-next last-Fri expiry (~30 DTE remaining at exit)
ENTRY_HOURS_IST_PER_CYCLE = {
    "monthly":           (23,),
    "bimonthly":         (23,),
    "lastfri_monthly":   (10,),
    # lastfri_bimonthly: enter at 18:00 IST = 12:30 UTC, AFTER Delta lists
    # the month-after-next expiry (which lists at 12:00 UTC on the prior
    # monthly's settlement day = the last-Friday itself). Entry at 10:00 IST
    # is BEFORE the listing → no viable strikes. See HANDOFF Session 25.
    "lastfri_bimonthly": (18,),
}
ENTRY_DAYS_PER_CYCLE = {
    "monthly":           ("mon",),
    "bimonthly":         ("mon",),
    "lastfri_monthly":   ("fri",),
    "lastfri_bimonthly": ("fri",),
}

DOW_OFFSETS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Strike-matching defaults (per-leg tolerance, max leg-to-leg delta gap, retry window).
MATCH_PER_LEG_TOL = 0.025      # |leg_delta - target_delta| must be ≤ this
MATCH_LEG_GAP = 0.020          # |call_delta - put_delta| must be ≤ this
MATCH_MAX_WAIT_MIN = 60        # max minutes to wait past requested entry
MATCH_RETRY_INTERVAL_MIN = 5   # chain snapshots are 5m anyway

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Date enumerators ─────────────────────────────────────────────────────────

def last_friday_of_month(year: int, month: int) -> date:
    """Last Friday of given (year, month)."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != 4:
        d -= timedelta(days=1)
    return d


def first_monday_of_month(year: int, month: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d


def first_mondays_in_range(start_unix: int, end_unix: int) -> list[date]:
    """First Monday of each month in [start, end] UTC."""
    out: list[date] = []
    cur = datetime.fromtimestamp(start_unix, tz=timezone.utc).date()
    end_d = datetime.fromtimestamp(end_unix, tz=timezone.utc).date()
    m = date(cur.year, cur.month, 1)
    while m <= end_d:
        fm = first_monday_of_month(m.year, m.month)
        if cur <= fm <= end_d:
            out.append(fm)
        if m.month == 12:
            m = date(m.year + 1, 1, 1)
        else:
            m = date(m.year, m.month + 1, 1)
    return out


def last_fridays_in_range(start_unix: int, end_unix: int) -> list[date]:
    """Last Friday of each month in [start, end] UTC."""
    out: list[date] = []
    cur = datetime.fromtimestamp(start_unix, tz=timezone.utc).date()
    end_d = datetime.fromtimestamp(end_unix, tz=timezone.utc).date()
    m = date(cur.year, cur.month, 1)
    while m <= end_d:
        lf = last_friday_of_month(m.year, m.month)
        if cur <= lf <= end_d:
            out.append(lf)
        if m.month == 12:
            m = date(m.year + 1, 1, 1)
        else:
            m = date(m.year, m.month + 1, 1)
    return out


def next_last_friday(d: date) -> date:
    """Last Friday of the NEXT calendar month after d."""
    if d.month == 12:
        return last_friday_of_month(d.year + 1, 1)
    return last_friday_of_month(d.year, d.month + 1)


def next_to_next_last_friday(d: date) -> date:
    """Last Friday of the month-after-next."""
    m = d.month + 2
    y = d.year
    if m > 12:
        m -= 12
        y += 1
    return last_friday_of_month(y, m)


# ── Entry/exit timestamp helpers ─────────────────────────────────────────────

def _ist_hour_to_utc_minutes(hour_ist: int) -> tuple[int, int]:
    """Convert an IST hour-of-day to (utc_minute_offset, utc_day_offset_vs_ist_date).

    IST = UTC + 5:30. Some IST hours roll back to the previous UTC date.
    Returns (minutes_in_utc_day, ist_day_offset) where ist_day_offset is 0 if
    the IST date and UTC date match, else 0 (IST=00:00..05:29 IST is same UTC
    day as the IST date, since IST 00:00 = UTC 18:30 of previous day → no, we
    pick the UTC date that contains the IST hour).

    Simpler: just compute the absolute UTC instant for (ist_date 00:00 + hour
    in IST). IST 23:00 of date D = UTC (D 17:30). IST 10:00 of date D = UTC (D 04:30).
    """
    utc_minutes_from_ist_midnight = hour_ist * 60 - 330  # 5h30m IST offset
    return (utc_minutes_from_ist_midnight, 0)


def entry_ts_for_ist_date(ist_d: date, hour_ist: int) -> int:
    """Unix timestamp of (ist_d at hour_ist IST). Handles negative-minutes wrap."""
    utc_minutes_from_ist_midnight = hour_ist * 60 - 330
    # IST midnight of ist_d == UTC (ist_d - 0 days) at 18:30 of (ist_d - 1)?
    # No: IST 00:00 = UTC 18:30 of previous calendar date. So when we add the
    # IST hour-of-day in minutes, the resulting UTC timestamp is:
    #    midnight_utc(ist_d) - 5:30 + hour_ist
    # Which is the same as: midnight_utc(ist_d) + (hour_ist - 5.5)*60 min
    base_utc = datetime(ist_d.year, ist_d.month, ist_d.day,
                        0, 0, 0, tzinfo=timezone.utc)
    return int(base_utc.timestamp()) + utc_minutes_from_ist_midnight * 60


def exit_ts_for_cycle(entry_date: date, cycle: str) -> int:
    """Compute the exit unix timestamp for a given cycle.

    Monthly/Bimonthly         : last Friday of entry_date's month at 11:00 IST.
    Lastfri_monthly/bimonthly : last Friday of entry_date's NEXT month at 11:00 IST.
    """
    if cycle in ("monthly", "bimonthly"):
        lf = last_friday_of_month(entry_date.year, entry_date.month)
    elif cycle in ("lastfri_monthly", "lastfri_bimonthly"):
        lf = next_last_friday(entry_date)
    else:
        raise ValueError(f"unknown cycle: {cycle}")
    return entry_ts_for_ist_date(lf, 11)


def expiry_for_cycle(entry_date: date, cycle: str) -> date:
    """Pick the monthly expiry date sold by a given cycle.

    Monthly           : last-Fri of entry_date's month (= exit anchor, ~0 DTE at exit)
    Bimonthly         : last-Fri of entry_date's NEXT month (~30 DTE remaining at exit)
    Lastfri_monthly   : last-Fri of entry_date's NEXT month (= exit anchor, ~0 DTE at exit)
    Lastfri_bimonthly : last-Fri of entry_date's month-after-next (~30 DTE remaining at exit)
    """
    if cycle == "monthly":
        return last_friday_of_month(entry_date.year, entry_date.month)
    elif cycle == "bimonthly":
        return next_last_friday(entry_date)
    elif cycle == "lastfri_monthly":
        return next_last_friday(entry_date)
    elif cycle == "lastfri_bimonthly":
        return next_to_next_last_friday(entry_date)
    else:
        raise ValueError(f"unknown cycle: {cycle}")


def make_trade_id(cycle: str, entry_date: date, hour_ist: int,
                  expiry_iso: str, target_delta: float) -> int:
    s = f"{cycle}|{entry_date.isoformat()}|{hour_ist:02d}|{expiry_iso}|{target_delta:.2f}"
    h = hashlib.sha256(s.encode()).digest()
    return int.from_bytes(h[:8], "big") & ((1 << 63) - 1)


# ── Strike-matching entry policy ─────────────────────────────────────────────

def pick_strikes_with_match(
    chain_5m_aligned: pd.DataFrame,
    spot_series: pd.DataFrame,
    expiry_unix: int,
    target_delta: float,
    requested_entry_ts: int,
    walk_end_ts: int,
    *,
    per_leg_tol: float = MATCH_PER_LEG_TOL,
    leg_gap_tol: float = MATCH_LEG_GAP,
    max_wait_min: int = MATCH_MAX_WAIT_MIN,
    retry_interval_min: int = MATCH_RETRY_INTERVAL_MIN,
    match_mode: bool = True,
) -> Optional[dict]:
    """Try to enter at requested_entry_ts. If the picked legs don't both land
    within `per_leg_tol` of `target_delta` AND within `leg_gap_tol` of each
    other, retry every `retry_interval_min` for up to `max_wait_min` past
    requested_entry_ts. Bounded by `walk_end_ts`.

    Returns:
        dict with keys:
          - picks: same shape as `pick_strikes` output
          - actual_entry_ts: timestamp at which match succeeded
          - spot_at_entry: spot at actual_entry_ts
          - T_at_entry: time-to-expiry years at actual_entry_ts
          - match_quality: max(|call_Δ-T|, |put_Δ-T|, |call_Δ-put_Δ|)
          - wait_minutes: (actual - requested) / 60
          - skipped_reason: None on success
        OR None if no match within budget.

    For target_delta ≥ 0.495 (straddle), match logic is degenerate (single
    strike for both legs) — return the first-snap result without retry.
    """

    def _attempt(ts: int) -> Optional[dict]:
        # Snap to 5m chain boundary
        snap_ts = ts - (ts % 300)
        snap = chain_5m_aligned[chain_5m_aligned["timestamp_unix"] == snap_ts]
        if snap.empty:
            return None
        # Spot at attempt ts (1m bars)
        if ts in spot_series.index:
            sp = float(spot_series.loc[ts, "mark_close"])
        else:
            pos = spot_series.index.searchsorted(ts, side="right") - 1
            if pos < 0:
                return None
            sp = float(spot_series.iloc[pos]["mark_close"])
        if sp <= 0:
            return None
        T_t = (expiry_unix - ts) / (365.0 * 86400.0)
        if T_t <= 0:
            return None
        picks = pick_strikes(snap, sp, T_t, target_delta)
        if picks is None:
            return None
        return {"picks": picks, "actual_entry_ts": ts,
                "spot_at_entry": sp, "T_at_entry": T_t}

    # Straddle short-circuit
    if target_delta >= 0.495:
        r = _attempt(requested_entry_ts)
        if r is None:
            return None
        r["match_quality"] = 0.0
        r["wait_minutes"] = 0
        r["skipped_reason"] = None
        return r

    target = abs(target_delta)

    if not match_mode:
        # Backward-compat: single attempt at requested ts, no retry.
        r = _attempt(requested_entry_ts)
        if r is None:
            return None
        ca = abs(r["picks"]["call_delta"])
        pa = abs(r["picks"]["put_delta"])
        r["match_quality"] = float(max(abs(ca - target), abs(pa - target), abs(ca - pa)))
        r["wait_minutes"] = 0
        r["skipped_reason"] = None
        return r

    best_far: Optional[dict] = None
    best_quality = float("inf")

    for offset_min in range(0, max_wait_min + 1, retry_interval_min):
        ts = requested_entry_ts + offset_min * 60
        if ts >= walk_end_ts:
            break
        r = _attempt(ts)
        if r is None:
            continue
        ca = abs(r["picks"]["call_delta"])
        pa = abs(r["picks"]["put_delta"])
        gap = abs(ca - pa)
        cdiff = abs(ca - target)
        pdiff = abs(pa - target)
        quality = float(max(cdiff, pdiff, gap))
        # Track best-so-far in case we never meet tolerance
        if quality < best_quality:
            best_quality = quality
            best_far = r
            best_far["match_quality"] = quality
            best_far["wait_minutes"] = offset_min
        # Success?
        if cdiff <= per_leg_tol and pdiff <= per_leg_tol and gap <= leg_gap_tol:
            r["match_quality"] = quality
            r["wait_minutes"] = offset_min
            r["skipped_reason"] = None
            return r

    # No tolerance-passing match in window
    if best_far is not None:
        best_far["skipped_reason"] = "strike_unmatched"
        return best_far
    return None


# ── Per-trade builder (mirrors m7's build_trade) ─────────────────────────────

def build_trade(cycle: str, anchor_date: date, hour_ist: int, dow: str,
                expiry: date, target_delta: float,
                entry_ts: int, exit_cap_ts: int,
                spot_series: pd.DataFrame,
                chain_5m_aligned: pd.DataFrame,
                atm_iv_series: dict[int, float],
                conn: duckdb.DuckDBPyConnection,
                cost_cfg: dict,
                match_mode: bool = True) -> Optional[tuple[dict, list[dict]]]:
    """Simulate one m-month trade. Returns (trade_row, path_rows) or None on skip.

    With match_mode=True (default), runs the strike-matching policy:
    retries every 5 min for up to 60 min past `entry_ts` until both legs
    land within tolerance of `target_delta`. The realised entry timestamp
    may be later than `entry_ts`. With match_mode=False, picks once at
    `entry_ts` (legacy behaviour).
    """

    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    if expiry_unix <= entry_ts + 60:
        return None

    requested_entry_ts = int(entry_ts)
    walk_end = min(exit_cap_ts, expiry_unix - 60)

    match_result = pick_strikes_with_match(
        chain_5m_aligned, spot_series, expiry_unix, target_delta,
        requested_entry_ts, walk_end, match_mode=match_mode,
    )
    if match_result is None:
        return None
    if match_result.get("skipped_reason") == "strike_unmatched":
        return None  # Don't record skipped trades — they're not entered

    picks = match_result["picks"]
    entry_ts = int(match_result["actual_entry_ts"])
    spot_at_entry = float(match_result["spot_at_entry"])
    T_e = float(match_result["T_at_entry"])
    match_quality = float(match_result["match_quality"])
    wait_minutes = int(match_result["wait_minutes"])

    snap_ts = entry_ts - (entry_ts % 300)

    m3_row = _m3_at_or_before(entry_ts) or {}

    entry_atm_iv_dec = _ff_lookup(atm_iv_series, snap_ts)
    entry_atm_iv_pct = entry_atm_iv_dec * 100.0
    entry_atm_iv_band = iv_band_label(entry_atm_iv_pct)

    e_slip_c, e_brk_c = _entry_cost_breakdown(
        spot_at_entry, picks["call_mark"], picks["call_strike"], True,
        QTY_LOTS, entry_ts, cost_cfg,
    )
    e_slip_p, e_brk_p = _entry_cost_breakdown(
        spot_at_entry, picks["put_mark"], picks["put_strike"], False,
        QTY_LOTS, entry_ts, cost_cfg,
    )
    total_entry_cost = e_slip_c + e_brk_c + e_slip_p + e_brk_p

    margin = compute_entry_margin(spot_at_entry, picks, T_e, QTY_LOTS)

    try:
        cg_e = compute_greeks(spot_at_entry, picks["call_strike"], T_e, 0.0,
                              picks["call_iv"], "call") if picks["call_iv"] > 0 else None
        pg_e = compute_greeks(spot_at_entry, picks["put_strike"], T_e, 0.0,
                              picks["put_iv"], "put") if picks["put_iv"] > 0 else None
    except Exception:
        cg_e = pg_e = None
    cg_e_d = (cg_e.delta, cg_e.gamma, cg_e.theta, cg_e.vega) if cg_e else (0.0, 0.0, 0.0, 0.0)
    pg_e_d = (pg_e.delta, pg_e.gamma, pg_e.theta, pg_e.vega) if pg_e else (0.0, 0.0, 0.0, 0.0)

    total_credit = picks["call_mark"] + picks["put_mark"]
    credit_usd = total_credit * QTY_LOTS * CONTRACT_VALUE
    credit_pct_of_spot = total_credit / spot_at_entry if spot_at_entry > 0 else float("nan")
    dte_days = (expiry_unix - entry_ts) / 86400.0
    credit_pct_normalized = (
        credit_pct_of_spot / math.sqrt(max(dte_days, 1e-6))
        if dte_days > 0 else float("nan")
    )

    tid = make_trade_id(cycle, anchor_date, hour_ist, expiry_iso, target_delta)

    # ── Build path rows ────────────────────────────────────────────────────
    call_bars = load_leg_bars_1m(conn, expiry_iso, picks["call_strike"], "CE",
                                 entry_ts, walk_end)
    put_bars = load_leg_bars_1m(conn, expiry_iso, picks["put_strike"], "PE",
                                entry_ts, walk_end)
    if call_bars.empty or put_bars.empty:
        return None

    minute_grid = sorted(set(call_bars.index) | set(put_bars.index))
    minute_grid = [t for t in minute_grid if entry_ts <= t <= walk_end]
    if not minute_grid:
        return None

    path_rows: list[dict] = []
    last_call = picks["call_mark"]
    last_put = picks["put_mark"]
    last_call_oi = float(call_bars["oi_close"].iloc[0]) if "oi_close" in call_bars.columns else 0.0
    last_put_oi = float(put_bars["oi_close"].iloc[0]) if "oi_close" in put_bars.columns else 0.0

    call_idx = 0
    put_idx = 0
    call_ts_arr = call_bars.index.to_numpy()
    put_ts_arr = put_bars.index.to_numpy()

    for t in minute_grid:
        while call_idx < len(call_ts_arr) and int(call_ts_arr[call_idx]) <= t:
            r = call_bars.iloc[call_idx]
            last_call = float(r["mark_close"])
            v = r.get("oi_close", None)
            if v is not None and not pd.isna(v):
                last_call_oi = float(v)
            call_idx += 1
        while put_idx < len(put_ts_arr) and int(put_ts_arr[put_idx]) <= t:
            r = put_bars.iloc[put_idx]
            last_put = float(r["mark_close"])
            v = r.get("oi_close", None)
            if v is not None and not pd.isna(v):
                last_put_oi = float(v)
            put_idx += 1

        if t in spot_series.index:
            sp = float(spot_series.loc[t, "mark_close"])
        else:
            pos = spot_series.index.searchsorted(t, side="right") - 1
            if pos < 0:
                continue
            sp = float(spot_series.iloc[pos]["mark_close"])

        T_t = max(1e-6, (expiry_unix - t) / (365.0 * 86400.0))

        try:
            ci_t = implied_vol(last_call, sp, picks["call_strike"], T_t, 0.0, "call") or 0.0
        except Exception:
            ci_t = 0.0
        try:
            pi_t = implied_vol(last_put, sp, picks["put_strike"], T_t, 0.0, "put") or 0.0
        except Exception:
            pi_t = 0.0

        try:
            cg = compute_greeks(sp, picks["call_strike"], T_t, 0.0, ci_t, "call") if ci_t > 0 else None
        except Exception:
            cg = None
        try:
            pg = compute_greeks(sp, picks["put_strike"], T_t, 0.0, pi_t, "put") if pi_t > 0 else None
        except Exception:
            pg = None
        c_d = cg.delta if cg else 0.0
        c_g = cg.gamma if cg else 0.0
        c_th = cg.theta if cg else 0.0
        c_v = cg.vega if cg else 0.0
        p_d = pg.delta if pg else 0.0
        p_g = pg.gamma if pg else 0.0
        p_th = pg.theta if pg else 0.0
        p_v = pg.vega if pg else 0.0

        atm_iv_dec = _ff_lookup(atm_iv_series, t - (t % 300))

        gross_pnl = ((picks["call_mark"] - last_call) + (picks["put_mark"] - last_put)) \
            * QTY_LOTS * CONTRACT_VALUE
        net_pnl_unwind = gross_pnl - total_entry_cost

        pnl_pct_of_credit = (gross_pnl / credit_usd) * 100 if credit_usd > 0 else float("nan")
        pnl_pct_of_margin = (gross_pnl / margin) * 100 if margin and margin > 0 else float("nan")

        path_rows.append({
            "trade_id": tid,
            "ts": int(t),
            "minute_offset": int((t - entry_ts) // 60),
            "spot": sp,
            "call_mark": last_call, "put_mark": last_put,
            "total_premium": last_call + last_put,
            "call_oi": last_call_oi, "put_oi": last_put_oi,
            "call_iv": float(ci_t), "put_iv": float(pi_t),
            "atm_iv_now": float(atm_iv_dec),
            "call_delta": float(c_d), "call_gamma": float(c_g),
            "call_theta": float(c_th), "call_vega": float(c_v),
            "put_delta": float(p_d), "put_gamma": float(p_g),
            "put_theta": float(p_th), "put_vega": float(p_v),
            "net_delta": float(-(c_d + p_d)),
            "net_gamma": float(-(c_g + p_g)),
            "net_theta": float(-(c_th + p_th)),
            "net_vega": float(-(c_v + p_v)),
            "theta_per_vega_combined": (
                float((c_th + p_th) / (c_v + p_v))
                if abs(c_v + p_v) > 1e-9 else float("nan")
            ),
            "gross_pnl_usd": float(gross_pnl),
            "net_pnl_unwind_usd": float(net_pnl_unwind),
            "pnl_pct_of_credit": float(pnl_pct_of_credit),
            "pnl_pct_of_margin": float(pnl_pct_of_margin),
        })

    if not path_rows:
        return None

    ivp = m3_row.get("ivp_atm_7d_90d") if isinstance(m3_row, dict) else None
    ivp_val = float(ivp) if ivp is not None and not pd.isna(ivp) else float("nan")

    bucket_dte = _label_for_range(DTE_BUCKETS, dte_days)
    bucket_spot = _spot_label(spot_at_entry)
    bucket_delta = _delta_label(target_delta)
    bucket_ivp = _label_for_range(IVP_BUCKETS, ivp_val)

    entry_month = f"{anchor_date.year:04d}-{anchor_date.month:02d}"

    # Distinct labels per cycle so downstream can distinguish lastfri_bimonthly
    # (sells month-after-next expiry) from bimonthly (sells next-month expiry).
    _EXPIRY_VARIANT = {
        "monthly":           "monthly",
        "bimonthly":         "next_monthly",
        "lastfri_monthly":   "next_monthly",
        "lastfri_bimonthly": "after_next_monthly",
    }

    trade_row = {
        "trade_id": tid,
        "trade_cycle": cycle,
        "entry_month": entry_month,
        "anchor_date_ist": anchor_date.isoformat(),
        "entry_ts_utc": int(entry_ts),
        "entry_ts_requested_utc": int(requested_entry_ts),
        "entry_ts_actual_utc": int(entry_ts),
        "wait_minutes": int(wait_minutes),
        "match_quality": float(match_quality),
        "skipped_reason": None,
        "entry_hour_ist": int(hour_ist),
        "entry_dow": dow,
        "entry_time_label": f"{hour_ist:02d}:00",
        "expiry_variant": _EXPIRY_VARIANT[cycle],
        "expiry_date": expiry_iso,
        "expiry_unix": int(expiry_unix),
        "dte_hours_at_entry": float((expiry_unix - entry_ts) / 3600.0),
        "dte_days": float(dte_days),
        "delta_target": float(target_delta),
        "is_straddle": bool(target_delta >= 0.495),
        "quantity_lots": int(QTY_LOTS),
        "contract_size": float(CONTRACT_VALUE),
        "call_strike": int(picks["call_strike"]),
        "put_strike": int(picks["put_strike"]),
        "call_entry_mark": float(picks["call_mark"]),
        "put_entry_mark": float(picks["put_mark"]),
        "call_entry_iv": float(picks["call_iv"]),
        "put_entry_iv": float(picks["put_iv"]),
        "call_entry_delta": float(cg_e_d[0]),
        "put_entry_delta": float(pg_e_d[0]),
        "call_entry_gamma": float(cg_e_d[1]),
        "put_entry_gamma": float(pg_e_d[1]),
        "call_entry_theta": float(cg_e_d[2]),
        "put_entry_theta": float(pg_e_d[2]),
        "call_entry_vega": float(cg_e_d[3]),
        "put_entry_vega": float(pg_e_d[3]),
        "theta_per_vega_call": (float(cg_e_d[2] / cg_e_d[3]) if cg_e_d[3] else float("nan")),
        "theta_per_vega_put": (float(pg_e_d[2] / pg_e_d[3]) if pg_e_d[3] else float("nan")),
        "theta_per_vega_combined": (
            float((cg_e_d[2] + pg_e_d[2]) / (cg_e_d[3] + pg_e_d[3]))
            if abs(cg_e_d[3] + pg_e_d[3]) > 1e-9 else float("nan")
        ),
        "entry_net_delta": float(-(cg_e_d[0] + pg_e_d[0])),
        "entry_net_gamma": float(-(cg_e_d[1] + pg_e_d[1])),
        "entry_net_theta": float(-(cg_e_d[2] + pg_e_d[2])),
        "entry_net_vega": float(-(cg_e_d[3] + pg_e_d[3])),
        "total_credit_usd_per_btc": float(total_credit),
        "credit_usd": float(credit_usd),
        "credit_pct_of_spot": float(credit_pct_of_spot),
        "credit_pct_normalized": float(credit_pct_normalized),
        "spot_at_entry": float(spot_at_entry),
        "entry_atm_iv": float(entry_atm_iv_dec),
        "entry_atm_iv_pct": float(entry_atm_iv_pct),
        "entry_atm_iv_band": entry_atm_iv_band,
        "entry_slippage_call_usd": float(e_slip_c),
        "entry_slippage_put_usd": float(e_slip_p),
        "entry_brokerage_call_usd": float(e_brk_c),
        "entry_brokerage_put_usd": float(e_brk_p),
        "total_entry_cost_usd": float(total_entry_cost),
        "margin_used_usd_at_entry": float(margin) if margin else float("nan"),
        "dte_bucket": bucket_dte,
        "spot_bucket": bucket_spot,
        "delta_target_bucket": bucket_delta,
        "ivp_bucket": bucket_ivp,
        "n_path_rows": int(len(path_rows)),
        "path_first_ts": int(path_rows[0]["ts"]),
        "path_last_ts": int(path_rows[-1]["ts"]),
        "schema_version": 1,
    }
    for c in M3_CONTEXT_COLS:
        v = m3_row.get(c) if isinstance(m3_row, dict) else None
        if v is None or (isinstance(v, float) and pd.isna(v)):
            trade_row[f"ctx_{c}"] = None if not isinstance(v, (int, float)) else float("nan")
        else:
            trade_row[f"ctx_{c}"] = v

    return trade_row, path_rows


# ── Per-cycle × per-anchor-date processor ────────────────────────────────────

def _process_cycle_anchor(cycle: str, anchor_date: date,
                          cost_cfg: dict,
                          conn: duckdb.DuckDBPyConnection,
                          match_mode: bool = True,
                          ) -> tuple[list[dict], list[dict]]:
    """Simulate every (entry_hour × dow_offset × delta) trade for one cycle anchor."""
    expiry = expiry_for_cycle(anchor_date, cycle)
    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    hours = ENTRY_HOURS_IST_PER_CYCLE[cycle]
    days = ENTRY_DAYS_PER_CYCLE[cycle]

    earliest_hour = min(hours)
    latest_hour = max(hours)
    earliest_dow = min(DOW_OFFSETS[d] for d in days)
    latest_dow = max(DOW_OFFSETS[d] for d in days)

    if cycle in ("monthly", "bimonthly"):
        # Anchor is first-Mon; allow dow offsets 0..6 within same week.
        earliest_entry_date = anchor_date + timedelta(days=earliest_dow - DOW_OFFSETS[days[0]])
    else:
        earliest_entry_date = anchor_date  # last-Fri fixed

    win_start = entry_ts_for_ist_date(earliest_entry_date, earliest_hour)
    win_end = exit_ts_for_cycle(anchor_date, cycle)
    if expiry_unix <= win_start + 60:
        return [], []

    chain = load_chain_for_expiry(conn, expiry, win_start, win_end)
    if chain.empty:
        log.warning(f"  no chain rows for cycle={cycle} anchor={anchor_date} expiry={expiry_iso}")
        return [], []
    chain_5m = chain[chain["timestamp_unix"] % 300 == 0]
    if chain_5m.empty:
        return [], []

    spot_series = load_spot_window(conn, win_start - 600, win_end + 600)
    if spot_series.empty:
        return [], []

    atm_iv_series = compute_atm_iv_series(chain_5m, spot_series, expiry_unix)

    trades: list[dict] = []
    paths: list[dict] = []

    for hour_ist in hours:
        for dow in days:
            if cycle in ("monthly", "bimonthly"):
                # Anchor day is Mon (dow=0); shift by offset.
                dow_offset = DOW_OFFSETS[dow] - 0
                entry_date = anchor_date + timedelta(days=dow_offset)
            else:
                # Last-Fri rolling; anchor is Fri itself.
                entry_date = anchor_date
            entry_ts = entry_ts_for_ist_date(entry_date, hour_ist)
            if entry_ts >= win_end:
                continue
            for td in TARGET_DELTAS:
                try:
                    result = build_trade(
                        cycle, anchor_date, hour_ist, dow,
                        expiry, td,
                        entry_ts, win_end,
                        spot_series, chain_5m, atm_iv_series,
                        conn, cost_cfg,
                        match_mode=match_mode,
                    )
                except Exception as e:
                    log.warning(f"  trade failed cycle={cycle} anchor={anchor_date} "
                                f"hour={hour_ist} td={td}: {e}")
                    continue
                if result is None:
                    continue
                trade_row, path_rows = result
                trades.append(trade_row)
                paths.extend(path_rows)
    return trades, paths


# ── Main loop ────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    log.info("M-Month batch backtester starting")
    log.info(f"  out dir         = {args.out_dir}")
    log.info(f"  target deltas   = {TARGET_DELTAS}")
    log.info(f"  cycles          = {args.cycles}")
    log.info(f"  qty lots        = {QTY_LOTS}")
    log.info(f"  match mode      = {'ON' if not getattr(args, 'no_match', False) else 'OFF (legacy)'}")
    for c in args.cycles:
        log.info(f"  {c:18s}: entry_hours={ENTRY_HOURS_IST_PER_CYCLE[c]}  "
                 f"days={ENTRY_DAYS_PER_CYCLE[c]}")
    log.info("─" * 60)

    m3 = _load_m3()
    t_min = int(m3.index.min())
    t_max = int(m3.index.max())

    if args.since:
        s_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        t_min = max(t_min, int(s_dt.timestamp()))
    if args.through:
        t_dt = datetime.strptime(args.through, "%Y-%m-%d").replace(
            tzinfo=timezone.utc) + timedelta(days=1)
        t_max = min(t_max, int(t_dt.timestamp()))

    cost_cfg = DEFAULT_COST_CFG
    os.makedirs(args.out_dir, exist_ok=True)
    paths_out_dir = os.path.join(args.out_dir, "m_month_paths")
    os.makedirs(paths_out_dir, exist_ok=True)

    # Collect (cycle, anchor_date) work items
    work: list[tuple[str, date]] = []
    for cycle in args.cycles:
        if cycle in ("monthly", "bimonthly"):
            anchors = first_mondays_in_range(t_min, t_max)
        elif cycle in ("lastfri_monthly", "lastfri_bimonthly"):
            anchors = last_fridays_in_range(t_min, t_max)
            # Skip anchors where the relevant expiry date lies past t_max
            if cycle == "lastfri_monthly":
                future_exp = lambda a: next_last_friday(a)
            else:
                future_exp = lambda a: next_to_next_last_friday(a)
            anchors = [a for a in anchors
                       if int(datetime(future_exp(a).year, future_exp(a).month,
                                       future_exp(a).day, 0, 0, 0,
                                       tzinfo=timezone.utc).timestamp()) <= t_max]
        else:
            continue
        for a in anchors:
            work.append((cycle, a))

    if args.max_months:
        work = work[: args.max_months]
    if not work:
        log.error("No work items in range; aborting.")
        return
    log.info(f"Work items: {len(work)}  ({work[0]} … {work[-1]})")

    # Resume mode: pre-load existing trades parquet, build a skip-set of
    # (cycle, anchor) tuples that already have rows.
    all_trades: list[dict] = []
    existing_anchors: set[tuple[str, str]] = set()
    trades_out_path = os.path.join(args.out_dir, "m_month_trades.parquet")
    if getattr(args, "resume", False) and os.path.exists(trades_out_path):
        existing_df = pd.read_parquet(trades_out_path)
        existing_anchors = set(
            zip(existing_df["trade_cycle"].astype(str),
                existing_df["anchor_date_ist"].astype(str))
        )
        all_trades = existing_df.to_dict(orient="records")
        log.info(f"Resume: loaded {len(existing_df):,} existing trades; "
                 f"will skip {len(existing_anchors)} (cycle, anchor) tuples.")

    for wi, (cycle, anchor) in enumerate(work, 1):
        if (cycle, anchor.isoformat()) in existing_anchors:
            log.info(f"  [{wi}/{len(work)}] SKIP (resume) cycle={cycle} anchor={anchor}")
            continue
        conn = duckdb.connect()
        try:
            trades, paths = _process_cycle_anchor(
                cycle, anchor, cost_cfg, conn,
                match_mode=not getattr(args, "no_match", False),
            )
        except Exception as e:
            log.exception(f"  cycle={cycle} anchor={anchor} failed: {e}")
            conn.close()
            continue
        conn.close()

        all_trades.extend(trades)
        # Write THIS anchor's paths to a per-anchor file so concurrent (cycle,
        # anchor) tuples that share the same entry_month don't clobber each
        # other. Hive glob `entry_month=*/*.parquet` picks them all up.
        if trades and paths:
            em = trades[0]["entry_month"]
            part_dir = os.path.join(paths_out_dir, f"entry_month={em}")
            os.makedirs(part_dir, exist_ok=True)
            part_file = os.path.join(part_dir, f"part_{cycle}_{anchor.isoformat()}.parquet")
            tmp = part_file + ".tmp"
            pd.DataFrame(paths).to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, part_file)

        log.info(f"  [{wi}/{len(work)}] cycle={cycle} anchor={anchor}: "
                 f"{len(trades)} trades, {len(paths)} path rows")

        # Incremental trades-parquet snapshot every 5 work items
        if (wi % 5 == 0 or wi == len(work)) and all_trades:
            tdf = pd.DataFrame(all_trades)
            out = os.path.join(args.out_dir, "m_month_trades.parquet")
            tmp = out + ".tmp"
            tdf.to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, out)
            log.info(f"    → m_month_trades.parquet snapshot: {len(tdf):,} rows")

    if not all_trades:
        log.error("No trades produced; aborting trades-parquet write.")
        return

    trades_df = pd.DataFrame(all_trades)
    out = os.path.join(args.out_dir, "m_month_trades.parquet")
    tmp = out + ".tmp"
    trades_df.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, out)

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"M-Month done in {elapsed:.1f}s")
    log.info(f"  trades = {len(trades_df):,} → {out}")
    log.info(f"  paths partitioned by entry_month under: {paths_out_dir}")


# ── CLI ──────────────────────────────────────────────────────────────────────

VALID_CYCLES = ("monthly", "bimonthly", "lastfri_monthly", "lastfri_bimonthly")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None,
                   help="YYYY-MM-DD start (UTC).")
    p.add_argument("--through", type=str, default=None,
                   help="YYYY-MM-DD end (UTC, inclusive).")
    p.add_argument("--max-months", type=int, default=None,
                   help="Limit to first N (cycle, anchor) work items (debugging).")
    p.add_argument("--cycles", type=str, default=",".join(VALID_CYCLES),
                   help=f"Comma-separated cycles to run. Default: all. Valid: {VALID_CYCLES}")
    p.add_argument("--out-dir", type=str, default=M_MONTH_OUT_DIR,
                   help=f"Output directory. Default: {M_MONTH_OUT_DIR}.")
    p.add_argument("--no-match", action="store_true",
                   help="Disable strike-matching policy (single attempt at requested ts, legacy behaviour).")
    p.add_argument("--resume", action="store_true",
                   help="Resume mode: load existing m_month_trades.parquet, skip any "
                        "(cycle, anchor) tuple already represented. Path files are "
                        "left as-is (per-anchor parquets won't be overwritten).")
    a = p.parse_args()
    a.cycles = [c.strip() for c in a.cycles.split(",") if c.strip()]
    for c in a.cycles:
        if c not in VALID_CYCLES:
            p.error(f"unknown cycle: {c}")
    return a


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"M-Month failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
