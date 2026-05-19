"""
M9 — Friday-entry weekly / biweekly strangle sweep backtester.

Sibling to m_month_batch_backtester.py. Sells a Friday-expiry contract held
through the weekend and most of the following week (up to 6 days):

  weekly   : Friday entry → sells the next Friday's expiry (smallest Fri
             expiry with DTE-at-entry ≥ 7d). ~7 DTE at entry, ~1 DTE at
             6-day exit.
  biweekly : Friday entry → sells the Friday-after-next expiry (smallest
             Fri expiry with DTE-at-entry ≥ 14d). ~14 DTE at entry, ~8 DTE
             at 6-day exit.

Both expiry types are walked once per (Friday, entry_hour:minute IST,
target_delta), each for the full 6-day window (capped at expiry-1h).
Per-bar exit P&L for 1d / 2d / 3d / 4d / 5d / 6d / natural is derived
downstream from the recorded path by the best-combo API — we do NOT
run separate walks per hold duration.

Outputs (under /home/abhis/btc-data/derived/m9_friday_weekly/):
  m9_trades.parquet
  m9_paths/expiry_type={weekly|biweekly}/entry_yyyymm=YYYY-MM/part_<friday>.parquet

Run:
  python -m app.analytics.m9_friday_weekly_backtester                                # full
  python -m app.analytics.m9_friday_weekly_backtester --since 2024-01-05
  python -m app.analytics.m9_friday_weekly_backtester --max-fridays 3 --expiry-types weekly
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
    QTY_LOTS,
    _entry_cost_breakdown,
    _ff_lookup,
    _load_m3,
    _m3_at_or_before,
    compute_atm_iv_series,
    compute_entry_margin,
    fridays_in_range,
    iv_band_label,
    load_leg_bars_1m,
    load_spot_window,
)
from app.analytics.m_month_batch_backtester import pick_strikes_with_match
from app.core.greeks import compute_greeks, implied_vol
from app.services.costs import CONTRACT_VALUE

# ── Paths & constants ────────────────────────────────────────────────────────

M9_OUT_DIR = os.path.join(DERIVED_DIR, "m9_friday_weekly")
TRADES_OUT = os.path.join(M9_OUT_DIR, "m9_trades.parquet")
PATHS_OUT_DIR = os.path.join(M9_OUT_DIR, "m9_paths")

# (hour_ist, minute_ist). hour_ist=24 means 00:00 of the next IST day (same
# UTC date as the entry Friday, 18:30 UTC).
ENTRY_HOURS_MIN_IST: tuple[tuple[int, int], ...] = (
    (17, 30),
    (18, 0),
    (19, 0),
    (20, 0),
    (21, 0),
    (22, 0),
    (23, 0),
    (24, 0),
)

TARGET_DELTAS: tuple[float, ...] = (0.10, 0.20, 0.30, 0.40, 0.50)

# Walk every minute for up to 6 full days; downstream best-combo derives
# 1d/2d/3d/4d/5d/6d/natural exits from the same path.
MAX_HOLD_DAYS = 6
MAX_HOLD_SECONDS = MAX_HOLD_DAYS * 86400

# Strike-matching defaults (mirror M-Month).
MATCH_PER_LEG_TOL = 0.025
MATCH_LEG_GAP = 0.020
MATCH_MAX_WAIT_MIN = 60
MATCH_RETRY_INTERVAL_MIN = 5

VALID_EXPIRY_TYPES = ("weekly", "biweekly")
EXPIRY_MIN_DTE = {"weekly": 7, "biweekly": 14}

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Date / timestamp helpers ─────────────────────────────────────────────────

def entry_ts_for_friday_hm(friday: date, hour_ist: int, minute_ist: int) -> int:
    """Unix timestamp of (Friday at hour:minute IST).

    IST = UTC+5:30. All entry slots 17:30..24:00 IST land in the same UTC
    date as `friday` (12:00..18:30 UTC). hour_ist=24 represents 00:00 IST of
    the next calendar day; it converts to 18:30 UTC of `friday`.
    """
    utc_minutes_from_friday_midnight = hour_ist * 60 + minute_ist - 330
    base_utc = datetime(friday.year, friday.month, friday.day,
                        0, 0, 0, tzinfo=timezone.utc)
    return int(base_utc.timestamp()) + utc_minutes_from_friday_midnight * 60


def make_trade_id(expiry_type: str, friday: date, hour_ist: int, minute_ist: int,
                  expiry_iso: str, target_delta: float) -> int:
    s = (
        f"{expiry_type}|{friday.isoformat()}|"
        f"{hour_ist:02d}:{minute_ist:02d}|{expiry_iso}|{target_delta:.2f}"
    )
    h = hashlib.sha256(s.encode()).digest()
    return int.from_bytes(h[:8], "big") & ((1 << 63) - 1)


# Cache the listed-expiries set on first call so we don't hit the filesystem
# 121 × 2 = 242 times.
_LISTED_EXPIRIES: Optional[set[date]] = None


def _listed_friday_expiries() -> list[date]:
    global _LISTED_EXPIRIES
    if _LISTED_EXPIRIES is None:
        _LISTED_EXPIRIES = set(list_expiries())
    return sorted(d for d in _LISTED_EXPIRIES if d.weekday() == 4)


def pick_friday_expiry(entry_ts: int, min_dte_days: int) -> Optional[date]:
    """Smallest listed Friday expiry where (expiry_unix - entry_ts)/86400 ≥ min_dte_days.

    Walks forward through the discovered Friday expiries from the options/
    directory. Returns None if no qualifying expiry exists.
    """
    for fri in _listed_friday_expiries():
        exp_unix = expiry_dt_unix(fri)
        dte_days = (exp_unix - entry_ts) / 86400.0
        if dte_days >= min_dte_days:
            return fri
    return None


# ── Per-trade builder (fork of m_month.build_trade with M9 schema) ───────────

def build_trade(expiry_type: str, friday: date, hour_ist: int, minute_ist: int,
                expiry: date, target_delta: float,
                entry_ts: int, exit_cap_ts: int,
                spot_series: pd.DataFrame,
                chain_5m_aligned: pd.DataFrame,
                atm_iv_series: dict[int, float],
                conn: duckdb.DuckDBPyConnection,
                cost_cfg: dict,
                match_mode: bool = True) -> Optional[tuple[dict, list[dict]]]:
    """Simulate one M9 trade. Returns (trade_row, path_rows) or None on skip."""

    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    if expiry_unix <= entry_ts + 60:
        return None

    requested_entry_ts = int(entry_ts)
    walk_end = min(exit_cap_ts, expiry_unix - 60)

    match_result = pick_strikes_with_match(
        chain_5m_aligned, spot_series, expiry_unix, target_delta,
        requested_entry_ts, walk_end,
        per_leg_tol=MATCH_PER_LEG_TOL,
        leg_gap_tol=MATCH_LEG_GAP,
        max_wait_min=MATCH_MAX_WAIT_MIN,
        retry_interval_min=MATCH_RETRY_INTERVAL_MIN,
        match_mode=match_mode,
    )
    if match_result is None:
        return None
    if match_result.get("skipped_reason") == "strike_unmatched":
        return None

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

    tid = make_trade_id(expiry_type, friday, hour_ist, minute_ist,
                        expiry_iso, target_delta)

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

    entry_yyyymm = f"{friday.year:04d}-{friday.month:02d}"
    entry_time_label = f"{hour_ist:02d}:{minute_ist:02d}"

    trade_row = {
        "trade_id": tid,
        "expiry_type": expiry_type,                 # "weekly" | "biweekly"
        "entry_friday_ist": friday.isoformat(),
        "entry_yyyymm": entry_yyyymm,
        "entry_ts_utc": int(entry_ts),
        "entry_ts_requested_utc": int(requested_entry_ts),
        "entry_ts_actual_utc": int(entry_ts),
        "wait_minutes": int(wait_minutes),
        "match_quality": float(match_quality),
        "skipped_reason": None,
        "entry_hour_ist": int(hour_ist),
        "entry_minute_ist": int(minute_ist),
        "entry_time_label": entry_time_label,
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


# ── Per-(Friday, expiry_type) processor ──────────────────────────────────────

def _process_friday_expiry(friday: date, expiry_type: str,
                           cost_cfg: dict,
                           conn: duckdb.DuckDBPyConnection,
                           match_mode: bool = True,
                           ) -> tuple[list[dict], list[dict]]:
    """Simulate every (entry hour:minute × delta) trade for one (Friday, expiry_type)."""
    earliest_entry_ts = entry_ts_for_friday_hm(friday, *ENTRY_HOURS_MIN_IST[0])
    latest_entry_ts = entry_ts_for_friday_hm(friday, *ENTRY_HOURS_MIN_IST[-1])

    expiry = pick_friday_expiry(earliest_entry_ts, EXPIRY_MIN_DTE[expiry_type])
    if expiry is None:
        log.info(f"  no Friday expiry ≥{EXPIRY_MIN_DTE[expiry_type]}d listed for {friday} / {expiry_type}")
        return [], []
    expiry_unix = expiry_dt_unix(expiry)

    win_start = earliest_entry_ts
    win_end = min(latest_entry_ts + MAX_HOLD_SECONDS, expiry_unix - 60)
    if win_end <= win_start:
        return [], []

    chain = load_chain_for_expiry(conn, expiry, win_start, win_end)
    if chain.empty:
        log.warning(f"  no chain rows for {friday} {expiry_type} → expiry={expiry.isoformat()}")
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

    for (h, m) in ENTRY_HOURS_MIN_IST:
        entry_ts = entry_ts_for_friday_hm(friday, h, m)
        if entry_ts >= expiry_unix - 60:
            continue
        exit_cap_ts = min(entry_ts + MAX_HOLD_SECONDS, expiry_unix - 60)
        for td in TARGET_DELTAS:
            try:
                result = build_trade(
                    expiry_type, friday, h, m,
                    expiry, td,
                    entry_ts, exit_cap_ts,
                    spot_series, chain_5m, atm_iv_series,
                    conn, cost_cfg,
                    match_mode=match_mode,
                )
            except Exception as e:
                log.warning(f"  trade failed {friday} {expiry_type} {h:02d}:{m:02d} td={td}: {e}")
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
    log.info("M9 Friday Weekly/Biweekly backtester starting")
    log.info(f"  out dir         = {args.out_dir}")
    log.info(f"  target deltas   = {TARGET_DELTAS}")
    log.info(f"  entry hours IST = {ENTRY_HOURS_MIN_IST}")
    log.info(f"  expiry types    = {args.expiry_types}")
    log.info(f"  qty lots        = {QTY_LOTS}")
    log.info(f"  match mode      = {'ON' if not getattr(args, 'no_match', False) else 'OFF (legacy)'}")
    log.info(f"  max hold        = {MAX_HOLD_DAYS}d ({MAX_HOLD_SECONDS}s)")
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

    fridays = fridays_in_range(t_min, t_max)
    if args.max_fridays:
        fridays = fridays[: args.max_fridays]
    if not fridays:
        log.error("No Fridays in range; aborting.")
        return
    log.info(f"Fridays in range: {len(fridays)} ({fridays[0]} … {fridays[-1]})")

    cost_cfg = DEFAULT_COST_CFG
    os.makedirs(args.out_dir, exist_ok=True)
    paths_out_dir = os.path.join(args.out_dir, "m9_paths")
    os.makedirs(paths_out_dir, exist_ok=True)
    trades_out_path = os.path.join(args.out_dir, "m9_trades.parquet")

    # Resume mode: skip-set of (expiry_type, friday) tuples already present.
    all_trades: list[dict] = []
    existing_keys: set[tuple[str, str]] = set()
    if getattr(args, "resume", False) and os.path.exists(trades_out_path):
        existing_df = pd.read_parquet(trades_out_path)
        existing_keys = set(
            zip(existing_df["expiry_type"].astype(str),
                existing_df["entry_friday_ist"].astype(str))
        )
        all_trades = existing_df.to_dict(orient="records")
        log.info(f"Resume: loaded {len(existing_df):,} existing trades; "
                 f"will skip {len(existing_keys)} (expiry_type, friday) tuples.")

    # Total work items = fridays × expiry_types
    work: list[tuple[date, str]] = []
    for fri in fridays:
        for et in args.expiry_types:
            work.append((fri, et))
    log.info(f"Work items: {len(work)}  ({work[0]} … {work[-1]})")

    for wi, (friday, expiry_type) in enumerate(work, 1):
        if (expiry_type, friday.isoformat()) in existing_keys:
            log.info(f"  [{wi}/{len(work)}] SKIP (resume) {expiry_type} {friday}")
            continue
        conn = duckdb.connect()
        try:
            trades, paths = _process_friday_expiry(
                friday, expiry_type, cost_cfg, conn,
                match_mode=not getattr(args, "no_match", False),
            )
        except Exception as e:
            log.exception(f"  {expiry_type} {friday} failed: {e}")
            conn.close()
            continue
        conn.close()

        all_trades.extend(trades)
        if trades and paths:
            ym = trades[0]["entry_yyyymm"]
            part_dir = os.path.join(
                paths_out_dir,
                f"expiry_type={expiry_type}",
                f"entry_yyyymm={ym}",
            )
            os.makedirs(part_dir, exist_ok=True)
            part_file = os.path.join(part_dir, f"part_{friday.isoformat()}.parquet")
            tmp = part_file + ".tmp"
            pd.DataFrame(paths).to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, part_file)

        log.info(f"  [{wi}/{len(work)}] {expiry_type} {friday}: "
                 f"{len(trades)} trades, {len(paths)} path rows")

        # Incremental snapshot every 10 work items.
        if (wi % 10 == 0 or wi == len(work)) and all_trades:
            tdf = pd.DataFrame(all_trades)
            tmp = trades_out_path + ".tmp"
            tdf.to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, trades_out_path)
            log.info(f"    → m9_trades.parquet snapshot: {len(tdf):,} rows")

    if not all_trades:
        log.error("No trades produced; aborting trades-parquet write.")
        return

    trades_df = pd.DataFrame(all_trades)
    tmp = trades_out_path + ".tmp"
    trades_df.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, trades_out_path)

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"M9 done in {elapsed:.1f}s")
    log.info(f"  trades = {len(trades_df):,} → {trades_out_path}")
    log.info(f"  paths partitioned by expiry_type/entry_yyyymm under: {paths_out_dir}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None,
                   help="YYYY-MM-DD start (UTC).")
    p.add_argument("--through", type=str, default=None,
                   help="YYYY-MM-DD end (UTC, inclusive).")
    p.add_argument("--max-fridays", type=int, default=None,
                   help="Limit to first N Fridays in range (debugging).")
    p.add_argument("--expiry-types", type=str, default=",".join(VALID_EXPIRY_TYPES),
                   help=f"Comma-separated expiry types. Default: all. Valid: {VALID_EXPIRY_TYPES}")
    p.add_argument("--out-dir", type=str, default=M9_OUT_DIR,
                   help=f"Output directory. Default: {M9_OUT_DIR}.")
    p.add_argument("--no-match", action="store_true",
                   help="Disable strike-matching policy (single attempt at requested ts).")
    p.add_argument("--resume", action="store_true",
                   help="Resume mode: skip (expiry_type, friday) tuples already in m9_trades.parquet.")
    a = p.parse_args()
    a.expiry_types = [c.strip() for c in a.expiry_types.split(",") if c.strip()]
    for c in a.expiry_types:
        if c not in VALID_EXPIRY_TYPES:
            p.error(f"unknown expiry_type: {c}")
    return a


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"M9 failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
