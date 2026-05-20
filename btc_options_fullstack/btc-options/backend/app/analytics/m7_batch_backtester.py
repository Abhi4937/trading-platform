"""
M7 — Friday → Saturday strangle / straddle sweep backtester.

For each Friday in the dataset, simulate every (entry_time × expiry × delta_target)
combination as a SHORT strangle/straddle entered between Fri 21:00 IST and Sat 03:00
IST and held until Sat 17:30 IST. Records the full 1m path so that any exit
analysis (fixed-time, max-profit %, margin %, premium SL, or anything else) can
be derived later as a query against the path parquet.

NO exit logic in the simulator. Simulate once, query forever.

Strike-selection policy:
  - delta_target = 0.50   → true straddle: pick the single strike whose absolute
                            distance from current spot is smallest, use it for
                            BOTH CE and PE. Same strike, both legs.
  - delta_target < 0.50   → closest-from-below per leg: highest |delta| still
                            ≤ target on each side (always OTM). Vectorized
                            over the chain.

Outputs (under /home/abhis/btc-data/derived/m7/):
  m7_trades.parquet                          — one row per (entry, expiry, delta, friday)
  m7_paths/friday_date=YYYY-MM-DD/part.parquet — 1m path rows, Hive-partitioned

Run:
  python -m app.analytics.m7_batch_backtester --rebuild               # full overwrite
  python -m app.analytics.m7_batch_backtester --since 2024-01-01 --rebuild
  python -m app.analytics.m7_batch_backtester --max-fridays 1 --max-expiries 1 --rebuild

Incremental (append into existing parquet, idempotent on overlap):
  python -m app.analytics.m7_batch_backtester --since 2026-04-24 --append

When m7_trades.parquet already exists, --append or --rebuild is REQUIRED.
The script refuses to silently clobber history. --rebuild takes precedence
over --append if both are passed.
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
    SPOT_BUCKETS,
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
from app.core.greeks import compute_greeks, implied_vol
from app.services.costs import (
    CONTRACT_VALUE,
    compute_brokerage_one_side,
    slippage_dollars_per_side,
)
from app.services.margin_v2 import MarginLeg, compute_portfolio_margin

# ── Paths & constants ────────────────────────────────────────────────────────

M7_OUT_DIR = os.path.join(DERIVED_DIR, "m7")
TRADES_OUT = os.path.join(M7_OUT_DIR, "m7_trades.parquet")
PATHS_OUT_DIR = os.path.join(M7_OUT_DIR, "m7_paths")
FULL_ENRICHED_5M_PATH = os.path.join(DERIVED_DIR, "full_enriched_5m.parquet")
SPOT_PATH = "/home/abhis/btc-data/data/spot/BTCUSD_1min.parquet"

TARGET_DELTAS = (0.25, 0.30, 0.40, 0.50)  # 0.05-0.20 dropped; filtered at query time in m7_results.py
# Only generate expiries within the 4 kept DTE buckets (≤10 days).
# Biweekly/monthly/quarterly expiries are filtered at query time in m7_results.py
# anyway, but skipping them here saves ~40% of per-friday work on future --append runs.
MAX_EXPIRY_DTE_DAYS = 10
QTY_LOTS = 100

# Entry hours in IST. 21,22,23 fall on the same UTC date as the Friday;
# 0,1,2,3 are next IST day but still same UTC date as Friday (UTC+0 to UTC+5:30 lag).
# IST = UTC + 5:30 → IST 21:00 = UTC 15:30 (Fri); IST 03:00 = UTC 21:30 (Fri).
ENTRY_HOURS_IST = (21, 22, 23, 0, 1, 2, 3)

# Sat 17:30 IST = Sat 12:00 UTC (the hard cap for any exit derivation).
SAT_EXIT_HOUR_UTC = 12
SAT_EXIT_MIN_UTC = 0

# IV bands for the entry-time ATM IV (percent units, so iv_decimal × 100).
IV_BANDS = [
    (0, 20), (20, 30), (30, 40), (40, 50), (50, 60),
    (60, 70), (70, 80), (80, 90), (90, 100), (100, 100000),
]

DEFAULT_COST_CFG = {
    "slippage": {"enabled": True, "mode": "smart", "flat_value": 5.0, "mult": 1.0},
    "brokerage": {"enabled": True, "rate": "offer", "referral": False},
}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Time helpers ──────────────────────────────────────────────────────────────

def iv_band_label(iv_pct: float) -> str:
    if iv_pct is None or pd.isna(iv_pct):
        return "nan"
    for lo, hi in IV_BANDS:
        if lo <= iv_pct < hi:
            if hi >= 100000:
                return f"{int(lo)}+"
            return f"{int(lo)}-{int(hi)}"
    return "nan"


def fridays_in_range(start_unix: int, end_unix: int) -> list[date]:
    """All Fridays in [start, end] UTC (by date)."""
    out: list[date] = []
    cur = datetime.fromtimestamp(start_unix, tz=timezone.utc).date()
    while cur.weekday() != 4:
        cur += timedelta(days=1)
    end_date = datetime.fromtimestamp(end_unix, tz=timezone.utc).date()
    while cur <= end_date:
        out.append(cur)
        cur += timedelta(days=7)
    return out


def entry_ts_for_friday(friday: date, hour_ist: int) -> int:
    """IST entry hour → UTC unix timestamp.

    21,22,23 IST → same UTC date as `friday`, hour = (hour_ist - 5):30
    0,1,2,3 IST  → next IST day = same UTC date as `friday`, hour = (hour_ist + 18):30
    """
    if hour_ist >= 21:
        utc_minutes = (hour_ist - 5) * 60 - 30   # 21→930, 22→990, 23→1050
    else:
        utc_minutes = (hour_ist + 18) * 60 + 30  # 0→1110, 1→1170, 2→1230, 3→1290
    dt = datetime(friday.year, friday.month, friday.day, 0, 0, 0, tzinfo=timezone.utc)
    return int(dt.timestamp()) + int(utc_minutes * 60)


def sat_exit_ts_for_friday(friday: date) -> int:
    """Sat 17:30 IST = Sat 12:00 UTC."""
    sat = friday + timedelta(days=1)
    dt = datetime(sat.year, sat.month, sat.day,
                  SAT_EXIT_HOUR_UTC, SAT_EXIT_MIN_UTC, 0, tzinfo=timezone.utc)
    return int(dt.timestamp())


def make_trade_id(friday: date, hour_ist: int, expiry_iso: str, target_delta: float) -> int:
    s = f"{friday.isoformat()}|{hour_ist:02d}|{expiry_iso}|{target_delta:.2f}"
    h = hashlib.sha256(s.encode()).digest()
    # 63-bit unsigned to fit safely in pandas Int64
    return int.from_bytes(h[:8], "big") & ((1 << 63) - 1)


# ── M3 context cache ──────────────────────────────────────────────────────────

_M3_DF: pd.DataFrame | None = None


def _load_m3() -> pd.DataFrame:
    global _M3_DF
    if _M3_DF is None:
        if not os.path.exists(FULL_ENRICHED_5M_PATH):
            raise FileNotFoundError(
                f"M3 output not found at {FULL_ENRICHED_5M_PATH}. "
                f"Run `python -m app.analytics.enrich_derived --rebuild` first."
            )
        log.info("Loading M3 context table...")
        avail_cols = duckdb.sql(
            f"SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet('{FULL_ENRICHED_5M_PATH}'))"
        ).df()["column_name"].tolist()
        keep = ["timestamp_unix", "close"] + [c for c in M3_CONTEXT_COLS if c in avail_cols]
        _M3_DF = pd.read_parquet(FULL_ENRICHED_5M_PATH, columns=keep)
        _M3_DF = _M3_DF.set_index("timestamp_unix").sort_index()
        log.info(f"M3 rows: {len(_M3_DF):,}, cols: {len(_M3_DF.columns)}")
    return _M3_DF


def _m3_at_or_before(ts: int) -> dict | None:
    m3 = _load_m3()
    snap_ts = ts - (ts % 300)
    if snap_ts in m3.index:
        return m3.loc[snap_ts].to_dict()
    pos = m3.index.searchsorted(snap_ts, side="right") - 1
    if pos < 0:
        return None
    return m3.iloc[pos].to_dict()


# ── Spot 1m bars cache ────────────────────────────────────────────────────────

def load_spot_window(conn: duckdb.DuckDBPyConnection,
                     t_start: int, t_end: int) -> pd.DataFrame:
    """Return spot 1m OHLCV + OI for [t_start, t_end] indexed by timestamp_unix."""
    q = f"""
    SELECT timestamp_unix, mark_open, mark_high, mark_low, mark_close,
           ltp_volume, oi_close
    FROM read_parquet('{SPOT_PATH}')
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
    ORDER BY timestamp_unix ASC
    """
    df = conn.execute(q).df()
    if df.empty:
        return df
    df["timestamp_unix"] = df["timestamp_unix"].astype("int64")
    return df.set_index("timestamp_unix")


# ── Strike picker — vectorized two-mode ───────────────────────────────────────

def _bs_delta_vec(spot: float, strikes: np.ndarray, T_years: float,
                  ivs: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    """Vectorized BS delta. Returns NaN where ivs is invalid."""
    d = np.full_like(ivs, np.nan, dtype="float64")
    safe = (~np.isnan(ivs)) & (ivs > 0) & (strikes > 0) & (T_years > 0)
    if not np.any(safe):
        return d
    sq = math.sqrt(T_years)
    ks = strikes[safe]
    sigs = ivs[safe]
    d1 = (np.log(spot / ks) + 0.5 * sigs ** 2 * T_years) / (sigs * sq)
    # Abramowitz–Stegun N(d1) approximation
    x = d1 / math.sqrt(2.0)
    sign = np.where(x < 0, -1.0, 1.0)
    z = np.abs(x)
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    t_ = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t_ + a4) * t_) + a3) * t_ + a2) * t_ + a1) * t_ * np.exp(-z * z)
    N_d1 = 0.5 * (1.0 + sign * y)
    d_safe = np.where(is_call[safe], N_d1, N_d1 - 1.0)
    d[safe] = d_safe
    return d


def _solve_iv_vec(marks: np.ndarray, spot: float, strikes: np.ndarray,
                  T_years: float, is_call: np.ndarray) -> np.ndarray:
    """Per-row implied_vol() loop. Slow but correct; only called ~80 strikes per snapshot."""
    out = np.full(len(marks), np.nan, dtype="float64")
    for i in range(len(marks)):
        if marks[i] <= 0 or strikes[i] <= 0:
            continue
        flag = "call" if is_call[i] else "put"
        try:
            iv = implied_vol(float(marks[i]), float(spot), float(strikes[i]),
                             float(T_years), 0.0, flag)
            if iv and iv > 0:
                out[i] = float(iv)
        except Exception:
            continue
    return out


def pick_strikes(chain_at_ts: pd.DataFrame, spot: float, T_years: float,
                 target_delta: float) -> Optional[dict]:
    """Apply M7 strike-selection policy.

    Returns: {call_strike, put_strike, call_mark, put_mark,
              call_iv, put_iv, call_delta, put_delta} or None on failure.
    """
    if chain_at_ts.empty or spot <= 0 or T_years <= 0:
        return None

    pivot = chain_at_ts.pivot_table(
        index="strike", columns="opt_type", values="mark_close", aggfunc="first"
    )
    if "CE" not in pivot.columns or "PE" not in pivot.columns:
        return None
    pivot = pivot.dropna(subset=["CE", "PE"], how="all").sort_index()
    if pivot.empty:
        return None
    strikes_arr = pivot.index.to_numpy(dtype="float64")
    ce_marks = pivot["CE"].to_numpy(dtype="float64")
    pe_marks = pivot["PE"].to_numpy(dtype="float64")

    if target_delta >= 0.495:
        # True straddle: same strike (closest to spot) for both legs.
        idx = int(np.argmin(np.abs(strikes_arr - spot)))
        if (np.isnan(ce_marks[idx]) or np.isnan(pe_marks[idx]) or
            ce_marks[idx] <= 0 or pe_marks[idx] <= 0):
            return None
        K = float(strikes_arr[idx])
        cm, pm = float(ce_marks[idx]), float(pe_marks[idx])
        try:
            ci = implied_vol(cm, spot, K, T_years, 0.0, "call") or 0.0
            pi = implied_vol(pm, spot, K, T_years, 0.0, "put") or 0.0
            cd = compute_greeks(spot, K, T_years, 0.0, ci, "call").delta if ci > 0 else 0.0
            pd_ = compute_greeks(spot, K, T_years, 0.0, pi, "put").delta if pi > 0 else 0.0
        except Exception:
            return None
        return {
            "call_strike": int(K), "put_strike": int(K),
            "call_mark": cm, "put_mark": pm,
            "call_iv": float(ci), "put_iv": float(pi),
            "call_delta": float(cd), "put_delta": float(pd_),
        }

    # Closest-from-below per leg (always OTM, never overshoots target).
    # If NO strike satisfies abs(delta) <= target with a valid mark+IV, skip the
    # trade entirely (per plan: skipped_reason="no_qualifying_strike").
    is_call_arr = np.array([True] * len(strikes_arr) + [False] * len(strikes_arr))
    Ks_all = np.concatenate([strikes_arr, strikes_arr])
    marks_all = np.concatenate([ce_marks, pe_marks])
    ivs = _solve_iv_vec(marks_all, spot, Ks_all, T_years, is_call_arr)
    deltas = _bs_delta_vec(spot, Ks_all, T_years, ivs, is_call_arr)

    n = len(strikes_arr)
    call_deltas = deltas[:n]
    put_deltas = deltas[n:]
    call_ivs = ivs[:n]
    put_ivs = ivs[n:]

    target = abs(target_delta)
    # Call side: highest |delta| ≤ target with valid mark
    cs_idx = -1
    cs_best_d = -1.0
    for i in range(n):
        if np.isnan(call_deltas[i]) or ce_marks[i] <= 0:
            continue
        ad = abs(call_deltas[i])
        if ad <= target and ad > cs_best_d:
            cs_best_d = ad
            cs_idx = i
    if cs_idx < 0:
        return None  # no qualifying call strike

    # Put side: same logic
    ps_idx = -1
    ps_best_d = -1.0
    for i in range(n):
        if np.isnan(put_deltas[i]) or pe_marks[i] <= 0:
            continue
        ad = abs(put_deltas[i])
        if ad <= target and ad > ps_best_d:
            ps_best_d = ad
            ps_idx = i
    if ps_idx < 0:
        return None  # no qualifying put strike

    return {
        "call_strike": int(strikes_arr[cs_idx]),
        "put_strike":  int(strikes_arr[ps_idx]),
        "call_mark":   float(ce_marks[cs_idx]),
        "put_mark":    float(pe_marks[ps_idx]),
        "call_iv":     float(call_ivs[cs_idx]) if not np.isnan(call_ivs[cs_idx]) else 0.0,
        "put_iv":      float(put_ivs[ps_idx])  if not np.isnan(put_ivs[ps_idx])  else 0.0,
        "call_delta":  float(call_deltas[cs_idx]),
        "put_delta":   float(put_deltas[ps_idx]),
    }


# ── ATM IV time series cache (per friday × expiry) ────────────────────────────

def compute_atm_iv_series(chain_5m_aligned: pd.DataFrame,
                          spot_series: pd.DataFrame,
                          expiry_unix: int) -> dict[int, float]:
    """Return {ts → atm_iv_decimal} sampled at every 5m chain timestamp.

    Used for the 'entry_atm_iv' lookup and for the 1m path's atm_iv_now column
    (we forward-fill from the latest 5m sample at each minute).
    """
    out: dict[int, float] = {}
    if chain_5m_aligned.empty:
        return out
    by_ts = chain_5m_aligned.groupby("timestamp_unix")
    for ts, grp in by_ts:
        ts_int = int(ts)
        if ts_int not in spot_series.index:
            # find nearest spot at or before
            pos = spot_series.index.searchsorted(ts_int, side="right") - 1
            if pos < 0:
                continue
            sp = float(spot_series.iloc[pos]["mark_close"])
        else:
            sp = float(spot_series.loc[ts_int, "mark_close"])
        if sp <= 0:
            continue
        T = (expiry_unix - ts_int) / (365.0 * 86400.0)
        if T <= 0:
            continue
        # Pick strike closest to spot among those with both CE and PE marks.
        pivot = grp.pivot_table(index="strike", columns="opt_type",
                                values="mark_close", aggfunc="first")
        if "CE" not in pivot.columns or "PE" not in pivot.columns:
            continue
        pivot = pivot.dropna(subset=["CE", "PE"], how="any")
        if pivot.empty:
            continue
        strikes = pivot.index.to_numpy(dtype="float64")
        idx = int(np.argmin(np.abs(strikes - sp)))
        K = float(strikes[idx])
        cm, pm = float(pivot.iloc[idx]["CE"]), float(pivot.iloc[idx]["PE"])
        if cm <= 0 or pm <= 0:
            continue
        try:
            ci = implied_vol(cm, sp, K, T, 0.0, "call") or 0.0
            pi = implied_vol(pm, sp, K, T, 0.0, "put")  or 0.0
        except Exception:
            continue
        ivs = [v for v in (ci, pi) if v > 0]
        if not ivs:
            continue
        out[ts_int] = sum(ivs) / len(ivs)
    return out


def _ff_lookup(series_dict: dict[int, float], ts: int) -> float:
    """Forward-fill: latest value at or before ts. 0.0 if none."""
    if not series_dict:
        return 0.0
    if ts in series_dict:
        return series_dict[ts]
    keys = sorted(series_dict.keys())
    pos = np.searchsorted(keys, ts, side="right") - 1
    if pos < 0:
        return 0.0
    return float(series_dict[keys[pos]])


# ── Per-leg 1m bar cache ──────────────────────────────────────────────────────

def load_leg_bars_1m(conn: duckdb.DuckDBPyConnection,
                     expiry_iso: str, strike: int, opt_type: str,
                     t_start: int, t_end: int) -> pd.DataFrame:
    """Per-leg 1m OHLC + OI for a single (expiry, strike, type)."""
    path = f"/home/abhis/btc-data/data/options/expiry={expiry_iso}/strike={strike}/{opt_type}.parquet"
    if not os.path.exists(path):
        return pd.DataFrame()
    q = f"""
    SELECT timestamp_unix,
           mark_open, mark_high, mark_low, mark_close,
           oi_close
    FROM read_parquet('{path}')
    WHERE timestamp_unix >= {int(t_start)} AND timestamp_unix <= {int(t_end)}
      AND mark_close IS NOT NULL
    ORDER BY timestamp_unix ASC
    """
    df = conn.execute(q).df()
    if df.empty:
        return df
    df["timestamp_unix"] = df["timestamp_unix"].astype("int64")
    return df.set_index("timestamp_unix")


# ── Cost helpers ──────────────────────────────────────────────────────────────

def _entry_cost_breakdown(spot: float, mark: float, strike: int, is_call: bool,
                           qty: int, ts: int, cost_cfg: dict) -> tuple[float, float]:
    """Return (slippage_usd, brokerage_usd) for ONE side (entry) of ONE leg."""
    sl = cost_cfg.get("slippage", {}) or {}
    br = cost_cfg.get("brokerage", {}) or {}
    slip = slippage_dollars_per_side(
        sl.get("enabled", False), sl.get("mode", "smart"),
        sl.get("flat_value", 5.0), sl.get("mult", 1.0),
        spot, mark, strike, is_call, qty, ts,
    )
    brok = 0.0
    if br.get("enabled", False):
        brok = compute_brokerage_one_side(
            spot, mark, qty, br.get("rate", "offer"), br.get("referral", False),
        )
    return float(slip), float(brok)


# ── Entry margin ──────────────────────────────────────────────────────────────

def compute_entry_margin(spot: float, picks: dict, T_years: float,
                          qty: int) -> Optional[float]:
    """29-scenario portfolio margin at entry for the (CE SELL + PE SELL) pair."""
    try:
        legs: list[MarginLeg] = []
        for opt_type, strike, mark, iv in (
            ("CE", picks["call_strike"], picks["call_mark"], picks["call_iv"]),
            ("PE", picks["put_strike"],  picks["put_mark"],  picks["put_iv"]),
        ):
            if iv <= 0:
                continue
            legs.append(MarginLeg(
                strike=float(strike),
                is_call=(opt_type == "CE"),
                is_buy=False,
                qty=int(qty),
                current_price=float(mark) * CONTRACT_VALUE,
                iv=float(iv),
                T=float(T_years),
                forward=0.0,
            ))
        if not legs:
            return None
        mr = compute_portfolio_margin(legs, spot)
        if mr is None:
            return None
        return round(mr.portfolio_margin, 2)
    except Exception:
        return None


# ── Per-trade builder ─────────────────────────────────────────────────────────

def build_trade(friday: date, hour_ist: int, expiry: date, target_delta: float,
                entry_ts: int, exit_cap_ts: int,
                spot_series: pd.DataFrame,
                chain_5m_aligned: pd.DataFrame,
                atm_iv_series: dict[int, float],
                conn: duckdb.DuckDBPyConnection,
                cost_cfg: dict) -> Optional[tuple[dict, list[dict]]]:
    """Simulate one trade. Returns (trade_row, path_rows) or None on skip."""

    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    # Skip if expiry is at/before exit cap (already-settled)
    if expiry_unix <= entry_ts + 60:
        return None

    # Snap to 5m boundary for chain lookup
    snap_ts = entry_ts - (entry_ts % 300)
    snap = chain_5m_aligned[chain_5m_aligned["timestamp_unix"] == snap_ts]
    if snap.empty:
        return None

    # Spot at entry
    if entry_ts in spot_series.index:
        spot_at_entry = float(spot_series.loc[entry_ts, "mark_close"])
    else:
        pos = spot_series.index.searchsorted(entry_ts, side="right") - 1
        if pos < 0:
            return None
        spot_at_entry = float(spot_series.iloc[pos]["mark_close"])
    if spot_at_entry <= 0:
        return None

    T_e = (expiry_unix - entry_ts) / (365.0 * 86400.0)
    if T_e <= 0:
        return None

    picks = pick_strikes(snap, spot_at_entry, T_e, target_delta)
    if picks is None:
        return None

    # M3 context at entry
    m3_row = _m3_at_or_before(entry_ts) or {}

    # Entry-time ATM IV (from precomputed series — forward-filled)
    entry_atm_iv_dec = _ff_lookup(atm_iv_series, snap_ts)
    entry_atm_iv_pct = entry_atm_iv_dec * 100.0
    entry_atm_iv_band = iv_band_label(entry_atm_iv_pct)

    # Per-leg cost breakdown at entry
    e_slip_c, e_brk_c = _entry_cost_breakdown(
        spot_at_entry, picks["call_mark"], picks["call_strike"], True,
        QTY_LOTS, entry_ts, cost_cfg,
    )
    e_slip_p, e_brk_p = _entry_cost_breakdown(
        spot_at_entry, picks["put_mark"], picks["put_strike"], False,
        QTY_LOTS, entry_ts, cost_cfg,
    )
    total_entry_cost = e_slip_c + e_brk_c + e_slip_p + e_brk_p

    # Margin
    margin = compute_entry_margin(spot_at_entry, picks, T_e, QTY_LOTS)

    # Entry greeks (full set)
    try:
        cg_e = compute_greeks(spot_at_entry, picks["call_strike"], T_e, 0.0,
                               picks["call_iv"], "call") if picks["call_iv"] > 0 else None
        pg_e = compute_greeks(spot_at_entry, picks["put_strike"],  T_e, 0.0,
                               picks["put_iv"],  "put")  if picks["put_iv"] > 0  else None
    except Exception:
        cg_e = pg_e = None
    cg_e_d = (cg_e.delta, cg_e.gamma, cg_e.theta, cg_e.vega) if cg_e else (0.0, 0.0, 0.0, 0.0)
    pg_e_d = (pg_e.delta, pg_e.gamma, pg_e.theta, pg_e.vega) if pg_e else (0.0, 0.0, 0.0, 0.0)

    total_credit = picks["call_mark"] + picks["put_mark"]
    credit_usd = total_credit * QTY_LOTS * CONTRACT_VALUE
    credit_pct_of_spot = total_credit / spot_at_entry if spot_at_entry > 0 else float("nan")
    dte_days = (expiry_unix - entry_ts) / 86400.0
    credit_pct_normalized = (credit_pct_of_spot / math.sqrt(max(dte_days, 1e-6))) if dte_days > 0 else float("nan")

    tid = make_trade_id(friday, hour_ist, expiry_iso, target_delta)

    # ── Build path rows ────────────────────────────────────────────────────
    # Load 1m bars per leg over [entry_ts, exit_cap_ts], capped at expiry_unix - 60
    walk_end = min(exit_cap_ts, expiry_unix - 60)
    call_bars = load_leg_bars_1m(conn, expiry_iso, picks["call_strike"], "CE",
                                  entry_ts, walk_end)
    put_bars  = load_leg_bars_1m(conn, expiry_iso, picks["put_strike"],  "PE",
                                  entry_ts, walk_end)
    if call_bars.empty or put_bars.empty:
        return None

    # Union of timestamps from both legs and spot
    minute_grid = sorted(set(call_bars.index) | set(put_bars.index))
    minute_grid = [t for t in minute_grid if entry_ts <= t <= walk_end]
    if not minute_grid:
        return None

    # Forward-fill last marks across the minute grid
    path_rows: list[dict] = []
    last_call = picks["call_mark"]
    last_put  = picks["put_mark"]
    last_call_oi = float(call_bars["oi_close"].iloc[0]) if "oi_close" in call_bars.columns else 0.0
    last_put_oi  = float(put_bars["oi_close"].iloc[0])  if "oi_close" in put_bars.columns  else 0.0

    call_idx = 0
    put_idx = 0
    call_ts_arr = call_bars.index.to_numpy()
    put_ts_arr  = put_bars.index.to_numpy()

    for t in minute_grid:
        # advance leg pointers to absorb any bars at-or-before t
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

        # spot at this minute
        if t in spot_series.index:
            sp = float(spot_series.loc[t, "mark_close"])
            sp_o = float(spot_series.loc[t, "mark_open"])
            sp_h = float(spot_series.loc[t, "mark_high"])
            sp_l = float(spot_series.loc[t, "mark_low"])
            sp_v = float(spot_series.loc[t, "ltp_volume"]) if not pd.isna(spot_series.loc[t, "ltp_volume"]) else 0.0
            sp_oi = float(spot_series.loc[t, "oi_close"]) if not pd.isna(spot_series.loc[t, "oi_close"]) else 0.0
        else:
            pos = spot_series.index.searchsorted(t, side="right") - 1
            if pos < 0:
                continue
            r = spot_series.iloc[pos]
            sp = float(r["mark_close"])
            sp_o, sp_h, sp_l = float(r["mark_open"]), float(r["mark_high"]), float(r["mark_low"])
            sp_v = float(r["ltp_volume"]) if not pd.isna(r["ltp_volume"]) else 0.0
            sp_oi = float(r["oi_close"]) if not pd.isna(r["oi_close"]) else 0.0

        T_t = max(1e-6, (expiry_unix - t) / (365.0 * 86400.0))

        # Solve IV per leg (close)
        try:
            ci_t = implied_vol(last_call, sp, picks["call_strike"], T_t, 0.0, "call") or 0.0
        except Exception:
            ci_t = 0.0
        try:
            pi_t = implied_vol(last_put,  sp, picks["put_strike"],  T_t, 0.0, "put") or 0.0
        except Exception:
            pi_t = 0.0

        # Greeks
        try:
            cg = compute_greeks(sp, picks["call_strike"], T_t, 0.0, ci_t, "call") if ci_t > 0 else None
        except Exception:
            cg = None
        try:
            pg = compute_greeks(sp, picks["put_strike"],  T_t, 0.0, pi_t, "put")  if pi_t > 0 else None
        except Exception:
            pg = None
        c_d = cg.delta if cg else 0.0; c_g = cg.gamma if cg else 0.0
        c_th = cg.theta if cg else 0.0; c_v = cg.vega if cg else 0.0
        p_d = pg.delta if pg else 0.0; p_g = pg.gamma if pg else 0.0
        p_th = pg.theta if pg else 0.0; p_v = pg.vega if pg else 0.0

        # ATM IV at this minute (5m forward-filled)
        atm_iv_dec = _ff_lookup(atm_iv_series, t - (t % 300))

        # P&L (gross): for SELL, profit = entry_mark - current_mark
        gross_pnl = ((picks["call_mark"] - last_call) + (picks["put_mark"] - last_put)) \
                    * QTY_LOTS * CONTRACT_VALUE
        # Net = gross minus entry costs (exit costs are derived at query time)
        net_pnl_unwind = gross_pnl - total_entry_cost

        pnl_pct_of_credit = (gross_pnl / credit_usd) * 100 if credit_usd > 0 else float("nan")
        pnl_pct_of_margin = (gross_pnl / margin) * 100 if margin and margin > 0 else float("nan")

        path_rows.append({
            "trade_id": tid,
            "ts": int(t),
            "minute_offset": int((t - entry_ts) // 60),
            # Spot OHLCV + OI
            "spot": sp, "spot_open": sp_o, "spot_high": sp_h, "spot_low": sp_l,
            "spot_volume": sp_v, "spot_oi": sp_oi,
            # Per-leg marks
            "call_mark": last_call, "put_mark": last_put,
            "total_premium": last_call + last_put,
            # Per-leg OI
            "call_oi": last_call_oi, "put_oi": last_put_oi,
            # Per-leg IV
            "call_iv": float(ci_t), "put_iv": float(pi_t),
            # ATM IV
            "atm_iv_now": float(atm_iv_dec),
            # Per-leg greeks
            "call_delta": float(c_d), "call_gamma": float(c_g),
            "call_theta": float(c_th), "call_vega": float(c_v),
            "put_delta": float(p_d), "put_gamma": float(p_g),
            "put_theta": float(p_th), "put_vega": float(p_v),
            # Net greeks (sell = negative position)
            "net_delta": float(-(c_d + p_d)),
            "net_gamma": float(-(c_g + p_g)),
            "net_theta": float(-(c_th + p_th)),
            "net_vega":  float(-(c_v + p_v)),
            # Greek ratios
            "theta_per_vega_combined": (
                float((c_th + p_th) / (c_v + p_v))
                if abs(c_v + p_v) > 1e-9 else float("nan")
            ),
            # P&L
            "gross_pnl_usd": float(gross_pnl),
            "net_pnl_unwind_usd": float(net_pnl_unwind),
            "pnl_pct_of_credit": float(pnl_pct_of_credit),
            "pnl_pct_of_margin": float(pnl_pct_of_margin),
        })

    if not path_rows:
        return None

    # ── Trade row ──────────────────────────────────────────────────────────
    ivp = m3_row.get("ivp_atm_7d_90d") if isinstance(m3_row, dict) else None
    ivp_val = float(ivp) if ivp is not None and not pd.isna(ivp) else float("nan")

    bucket_dte = _label_for_range(DTE_BUCKETS, dte_days)
    bucket_spot = _spot_label(spot_at_entry)
    bucket_delta = _delta_label(target_delta)
    bucket_ivp = _label_for_range(IVP_BUCKETS, ivp_val)

    trade_row = {
        "trade_id": tid,
        "friday_date_ist": friday.isoformat(),
        "entry_ts_utc": int(entry_ts),
        "entry_hour_ist": int(hour_ist),
        "entry_time_label": f"{hour_ist:02d}:00",
        "expiry_date": expiry_iso,
        "expiry_unix": int(expiry_unix),
        "dte_hours_at_entry": float((expiry_unix - entry_ts) / 3600.0),
        "dte_days": float(dte_days),
        "delta_target": float(target_delta),
        "is_straddle": bool(target_delta >= 0.495),
        "quantity_lots": int(QTY_LOTS),
        "contract_size": float(CONTRACT_VALUE),
        # Strikes + entry marks
        "call_strike": int(picks["call_strike"]),
        "put_strike":  int(picks["put_strike"]),
        "call_entry_mark": float(picks["call_mark"]),
        "put_entry_mark":  float(picks["put_mark"]),
        "call_entry_iv":   float(picks["call_iv"]),
        "put_entry_iv":    float(picks["put_iv"]),
        "call_entry_delta": float(cg_e_d[0]),
        "put_entry_delta":  float(pg_e_d[0]),
        "call_entry_gamma": float(cg_e_d[1]),
        "put_entry_gamma":  float(pg_e_d[1]),
        "call_entry_theta": float(cg_e_d[2]),
        "put_entry_theta":  float(pg_e_d[2]),
        "call_entry_vega":  float(cg_e_d[3]),
        "put_entry_vega":   float(pg_e_d[3]),
        # Greek ratios
        "theta_per_vega_call":     (float(cg_e_d[2]/cg_e_d[3]) if cg_e_d[3] else float("nan")),
        "theta_per_vega_put":      (float(pg_e_d[2]/pg_e_d[3]) if pg_e_d[3] else float("nan")),
        "theta_per_vega_combined": (
            float((cg_e_d[2]+pg_e_d[2]) / (cg_e_d[3]+pg_e_d[3]))
            if abs(cg_e_d[3]+pg_e_d[3]) > 1e-9 else float("nan")
        ),
        # Net entry greeks (sell = negative position)
        "entry_net_delta": float(-(cg_e_d[0]+pg_e_d[0])),
        "entry_net_gamma": float(-(cg_e_d[1]+pg_e_d[1])),
        "entry_net_theta": float(-(cg_e_d[2]+pg_e_d[2])),
        "entry_net_vega":  float(-(cg_e_d[3]+pg_e_d[3])),
        # Credit
        "total_credit_usd_per_btc": float(total_credit),
        "credit_usd": float(credit_usd),
        "credit_pct_of_spot": float(credit_pct_of_spot),
        "credit_pct_normalized": float(credit_pct_normalized),
        # Entry context
        "spot_at_entry": float(spot_at_entry),
        "entry_atm_iv":  float(entry_atm_iv_dec),
        "entry_atm_iv_pct": float(entry_atm_iv_pct),
        "entry_atm_iv_band": entry_atm_iv_band,
        # Costs
        "entry_slippage_call_usd": float(e_slip_c),
        "entry_slippage_put_usd":  float(e_slip_p),
        "entry_brokerage_call_usd": float(e_brk_c),
        "entry_brokerage_put_usd":  float(e_brk_p),
        "total_entry_cost_usd": float(total_entry_cost),
        # Margin
        "margin_used_usd_at_entry": float(margin) if margin else float("nan"),
        # Buckets (M3 / calibration_v2 join keys)
        "dte_bucket": bucket_dte,
        "spot_bucket": bucket_spot,
        "delta_target_bucket": bucket_delta,
        "ivp_bucket": bucket_ivp,
        # Path metadata
        "n_path_rows": int(len(path_rows)),
        "path_first_ts": int(path_rows[0]["ts"]),
        "path_last_ts":  int(path_rows[-1]["ts"]),
        "schema_version": 1,
    }
    # M3 context (frozen at entry)
    for c in M3_CONTEXT_COLS:
        v = m3_row.get(c) if isinstance(m3_row, dict) else None
        if v is None or (isinstance(v, float) and pd.isna(v)):
            trade_row[f"ctx_{c}"] = None if not isinstance(v, (int, float)) else float("nan")
        else:
            trade_row[f"ctx_{c}"] = v

    return trade_row, path_rows


# ── Per-friday × per-expiry processor ─────────────────────────────────────────

def _process_friday_expiry(friday: date, expiry: date,
                           cost_cfg: dict,
                           conn: duckdb.DuckDBPyConnection
                           ) -> tuple[list[dict], list[dict]]:
    """For one (friday, expiry): bulk-load chain + spot, then iterate entries × deltas."""
    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    # Window: from earliest entry (Fri 21:00 IST = Fri 15:30 UTC) to Sat 17:30 IST.
    win_start = entry_ts_for_friday(friday, ENTRY_HOURS_IST[0])
    win_end = sat_exit_ts_for_friday(friday)
    if expiry_unix <= win_start + 60:
        return [], []   # already-expired

    # Load chain at full resolution
    chain = load_chain_for_expiry(conn, expiry, win_start, win_end)
    if chain.empty:
        return [], []
    # 5m-aligned subset for strike resolution + ATM IV (M2's native compute granularity)
    chain_5m = chain[chain["timestamp_unix"] % 300 == 0]
    if chain_5m.empty:
        return [], []

    # Spot 1m series for the window (with a 10m buffer at each end)
    spot_series = load_spot_window(conn, win_start - 600, win_end + 600)
    if spot_series.empty:
        return [], []

    # ATM IV time series (5m granularity)
    atm_iv_series = compute_atm_iv_series(chain_5m, spot_series, expiry_unix)

    trades: list[dict] = []
    paths: list[dict] = []

    for hour_ist in ENTRY_HOURS_IST:
        entry_ts = entry_ts_for_friday(friday, hour_ist)
        for td in TARGET_DELTAS:
            try:
                result = build_trade(
                    friday, hour_ist, expiry, td,
                    entry_ts, win_end,
                    spot_series, chain_5m, atm_iv_series,
                    conn, cost_cfg,
                )
            except Exception as e:
                log.warning(f"  trade failed exp={expiry_iso} hour={hour_ist} td={td}: {e}")
                continue
            if result is None:
                continue
            trade_row, path_rows = result
            trades.append(trade_row)
            paths.extend(path_rows)
    return trades, paths


# ── Trades parquet write ──────────────────────────────────────────────────────

def _write_trades_atomic(
    new_rows: list[dict],
    out_path: str,
    *,
    append: bool,
) -> int:
    """Atomic write of the trades parquet. Returns final row count.

    append=True + target exists: read existing, drop rows whose
    `friday_date_ist` overlaps with new_rows (idempotent re-runs), concat,
    sort, atomic .tmp → rename.

    append=False or target missing: write new_rows only.

    Raises ValueError if new_rows is empty (callers should short-circuit
    before reaching here).
    """
    if not new_rows:
        raise ValueError("_write_trades_atomic: new_rows is empty")
    new_df = pd.DataFrame(new_rows)
    if append and os.path.exists(out_path):
        existing = pd.read_parquet(out_path)
        run_fridays = set(new_df["friday_date_ist"].unique())
        existing = existing[~existing["friday_date_ist"].isin(run_fridays)]
        merged = pd.concat([existing, new_df], ignore_index=True)
    else:
        merged = new_df
    sort_cols = [c for c in
                 ("friday_date_ist", "entry_hour_ist", "expiry_date", "delta_target")
                 if c in merged.columns]
    if sort_cols:
        merged = merged.sort_values(sort_cols).reset_index(drop=True)
    tmp = out_path + ".tmp"
    merged.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, out_path)
    return len(merged)


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    log.info("M7 batch backtester starting")
    log.info(f"  out dir         = {args.out_dir}")
    log.info(f"  target deltas   = {TARGET_DELTAS}")
    log.info(f"  qty lots        = {QTY_LOTS}")
    log.info(f"  entry hours IST = {ENTRY_HOURS_IST}")
    # --rebuild takes precedence over --append (explicit destructive intent).
    effective_append = bool(args.append and not args.rebuild)
    log.info(f"  mode            = {'append' if effective_append else 'rebuild'}")
    log.info("─" * 60)

    # Refuse to silently clobber an existing trades parquet — explicit
    # --append or --rebuild required when target exists.
    trades_out_path = os.path.join(args.out_dir, "m7_trades.parquet")
    if (os.path.exists(trades_out_path)
            and not args.append and not args.rebuild):
        raise RuntimeError(
            f"{trades_out_path} already exists. Pass --append to merge new "
            f"fridays in, or --rebuild to overwrite. Refusing to silently "
            f"clobber 121+ fridays of history."
        )

    m3 = _load_m3()
    t_min = int(m3.index.min())
    t_max = int(m3.index.max())

    if args.since:
        s_dt = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        t_min = max(t_min, int(s_dt.timestamp()))
    if args.through:
        t_dt = datetime.strptime(args.through, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        t_max = min(t_max, int(t_dt.timestamp()))

    fridays = fridays_in_range(t_min, t_max)
    if args.max_fridays:
        fridays = fridays[: args.max_fridays]
    if not fridays:
        log.error("No Fridays in range; aborting.")
        return
    log.info(f"Fridays to process: {len(fridays)}  ({fridays[0]} … {fridays[-1]})")

    # Discover expiries; only keep those whose settlement is between
    # the first Friday's entry start and ~90 days after the last Friday.
    expiries_all = list_expiries()
    fri_min_ts = entry_ts_for_friday(fridays[0], ENTRY_HOURS_IST[0])
    fri_max_ts = sat_exit_ts_for_friday(fridays[-1])
    expiries_filt = [
        e for e in expiries_all
        if expiry_dt_unix(e) > fri_min_ts and expiry_dt_unix(e) < fri_max_ts + 90 * 86400
    ]
    if args.max_expiries:
        expiries_filt = expiries_filt[: args.max_expiries]
    log.info(f"Expiries in window: {len(expiries_filt)}")

    cost_cfg = DEFAULT_COST_CFG
    os.makedirs(args.out_dir, exist_ok=True)
    paths_out_dir = os.path.join(args.out_dir, "m7_paths")
    os.makedirs(paths_out_dir, exist_ok=True)

    all_trades: list[dict] = []

    for fi, friday in enumerate(fridays, 1):
        conn = duckdb.connect()
        # For each Friday, restrict expiries to those that overlap [fri_entry_start, sat_exit + 60d]
        fri_start = entry_ts_for_friday(friday, ENTRY_HOURS_IST[0])
        fri_end = sat_exit_ts_for_friday(friday)
        # Expiry must be AFTER our entry start (otherwise no trade), and reasonable cap on far-future
        fri_expiries = [e for e in expiries_filt
                        if expiry_dt_unix(e) > fri_start + 60
                        and expiry_dt_unix(e) < fri_start + MAX_EXPIRY_DTE_DAYS * 86400]

        friday_paths: list[dict] = []
        friday_trades: list[dict] = []

        for ei, expiry in enumerate(fri_expiries, 1):
            try:
                trades, paths = _process_friday_expiry(friday, expiry, cost_cfg, conn)
            except Exception as e:
                log.exception(f"  friday={friday} expiry={expiry} failed: {e}")
                continue
            friday_trades.extend(trades)
            friday_paths.extend(paths)

        # Write this Friday's path partition
        if friday_paths:
            paths_df = pd.DataFrame(friday_paths)
            part_dir = os.path.join(paths_out_dir, f"friday_date={friday.isoformat()}")
            os.makedirs(part_dir, exist_ok=True)
            part_file = os.path.join(part_dir, "part.parquet")
            tmp = part_file + ".tmp"
            paths_df.to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, part_file)

        all_trades.extend(friday_trades)
        log.info(f"  [{fi}/{len(fridays)}] {friday}: {len(friday_trades)} trades, "
                 f"{len(friday_paths)} path rows")

        # Incremental trades-parquet write so the API/dashboard can query
        # partial results while the long backfill is still running.
        if all_trades and (fi % 5 == 0 or fi == len(fridays)):
            n = _write_trades_atomic(
                all_trades, trades_out_path, append=effective_append)
            log.info(f"    → m7_trades.parquet snapshot: {n:,} rows "
                     f"({'merged' if effective_append else 'overwrite'})")

        conn.close()

    if not all_trades:
        log.error("No trades produced; aborting trades-parquet write.")
        return

    # Final write
    n = _write_trades_atomic(all_trades, trades_out_path, append=effective_append)

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"M7 done in {elapsed:.1f}s")
    log.info(f"  trades = {n:,} → {trades_out_path}")
    log.info(f"  paths partitioned by friday under: {paths_out_dir}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None,
                   help="YYYY-MM-DD start (UTC). Default: M3 min.")
    p.add_argument("--through", type=str, default=None,
                   help="YYYY-MM-DD end (UTC, inclusive). Default: M3 max.")
    p.add_argument("--max-fridays", type=int, default=None,
                   help="Limit to first N Fridays (debugging).")
    p.add_argument("--max-expiries", type=int, default=None,
                   help="Limit to first N expiries (debugging).")
    p.add_argument("--out-dir", type=str, default=M7_OUT_DIR,
                   help=f"Output directory. Default: {M7_OUT_DIR}.")
    p.add_argument("--append", action="store_true",
                   help="Merge new fridays into existing m7_trades.parquet. "
                        "Idempotent on overlap: rows whose friday_date_ist "
                        "is in this run's range are dropped before concat.")
    p.add_argument("--rebuild", action="store_true",
                   help="Force overwrite of m7_trades.parquet, ignoring "
                        "--append. Explicit destructive flag.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"M7 failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
