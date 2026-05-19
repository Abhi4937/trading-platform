"""M7 joint-match (delta + price) batch backtester.

Mirrors `m7_batch_backtester.py` but uses `pick_strikes_joint()` for strike
selection, falling back to `pick_strikes()` (delta-only) when no joint pair
fits the price tolerance.

Outputs (under /home/abhis/btc-data/derived/m7/):
  m7_trades_price_matched.parquet
  m7_paths_price_matched/friday_date=YYYY-MM-DD/part.parquet

Append-safe trades-parquet writer (use --append for incremental runs).

Run:
  python -m app.analytics.m7_batch_backtester_joint
  python -m app.analytics.m7_batch_backtester_joint --since 2025-10-10 --through 2025-10-10
  python -m app.analytics.m7_batch_backtester_joint --since 2025-12-12 --append
  python -m app.analytics.m7_batch_backtester_joint --joint-delta-tol 0.07 --joint-price-tol-pct 0.20
"""
from __future__ import annotations

import argparse
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
)
from app.analytics.m7_batch_backtester import (
    CONTRACT_VALUE,
    DEFAULT_COST_CFG,
    ENTRY_HOURS_IST,
    QTY_LOTS,
    TARGET_DELTAS,
    _entry_cost_breakdown,
    _ff_lookup,
    _load_m3,
    _m3_at_or_before,
    compute_atm_iv_series,
    compute_entry_margin,
    entry_ts_for_friday,
    fridays_in_range,
    iv_band_label,
    load_chain_for_expiry,
    load_leg_bars_1m,
    load_spot_window,
    make_trade_id,
    pick_strikes,
    sat_exit_ts_for_friday,
)
from app.analytics.m7_strike_picker_joint import (
    JOINT_DELTA_TOL,
    JOINT_PRICE_TOL_PCT,
    pick_strikes_joint,
)
from app.core.greeks import compute_greeks, implied_vol

M7_OUT_DIR = os.path.join(DERIVED_DIR, "m7")
TRADES_OUT = os.path.join(M7_OUT_DIR, "m7_trades_price_matched.parquet")
PATHS_OUT_DIR = os.path.join(M7_OUT_DIR, "m7_paths_price_matched")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def build_trade(friday: date, hour_ist: int, expiry: date, target_delta: float,
                entry_ts: int, exit_cap_ts: int,
                spot_series: pd.DataFrame,
                chain_5m_aligned: pd.DataFrame,
                atm_iv_series: dict[int, float],
                conn: duckdb.DuckDBPyConnection,
                cost_cfg: dict,
                joint_delta_tol: float,
                joint_price_tol_pct: float) -> Optional[tuple[dict, list[dict]]]:
    """Simulate one trade using joint picker first, delta fallback on None."""
    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    if expiry_unix <= entry_ts + 60:
        return None

    snap_ts = entry_ts - (entry_ts % 300)
    snap = chain_5m_aligned[chain_5m_aligned["timestamp_unix"] == snap_ts]
    if snap.empty:
        return None

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

    # Joint picker first; on None, fall back to delta-only.
    picks = pick_strikes_joint(
        snap, spot_at_entry, T_e, target_delta,
        delta_tol=joint_delta_tol, price_tol_pct=joint_price_tol_pct,
    )
    match_mode = "joint"
    if picks is None:
        picks = pick_strikes(snap, spot_at_entry, T_e, target_delta)
        match_mode = "delta_fallback"
        if picks is None:
            return None
        cm = float(picks["call_mark"])
        pm = float(picks["put_mark"])
        diff = abs(cm - pm)
        mean_mark = (cm + pm) / 2.0
        diff_pct = (diff / mean_mark) if mean_mark > 0 else float("nan")
        picks["price_diff_usd"] = float(diff)
        picks["price_diff_pct"] = float(diff_pct)
        picks["delta_diff_call"] = float(abs(picks["call_delta"]) - target_delta)
        picks["delta_diff_put"] = float(abs(picks["put_delta"]) - target_delta)

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
    credit_pct_normalized = (credit_pct_of_spot / math.sqrt(max(dte_days, 1e-6))) if dte_days > 0 else float("nan")

    tid = make_trade_id(friday, hour_ist, expiry_iso, target_delta)

    walk_end = min(exit_cap_ts, expiry_unix - 60)
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
        c_d = cg.delta if cg else 0.0; c_g = cg.gamma if cg else 0.0
        c_th = cg.theta if cg else 0.0; c_v = cg.vega if cg else 0.0
        p_d = pg.delta if pg else 0.0; p_g = pg.gamma if pg else 0.0
        p_th = pg.theta if pg else 0.0; p_v = pg.vega if pg else 0.0

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
            "spot": sp, "spot_open": sp_o, "spot_high": sp_h, "spot_low": sp_l,
            "spot_volume": sp_v, "spot_oi": sp_oi,
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
        "theta_per_vega_call": (float(cg_e_d[2]/cg_e_d[3]) if cg_e_d[3] else float("nan")),
        "theta_per_vega_put": (float(pg_e_d[2]/pg_e_d[3]) if pg_e_d[3] else float("nan")),
        "theta_per_vega_combined": (
            float((cg_e_d[2]+pg_e_d[2]) / (cg_e_d[3]+pg_e_d[3]))
            if abs(cg_e_d[3]+pg_e_d[3]) > 1e-9 else float("nan")
        ),
        "entry_net_delta": float(-(cg_e_d[0]+pg_e_d[0])),
        "entry_net_gamma": float(-(cg_e_d[1]+pg_e_d[1])),
        "entry_net_theta": float(-(cg_e_d[2]+pg_e_d[2])),
        "entry_net_vega": float(-(cg_e_d[3]+pg_e_d[3])),
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
        # Joint-match metadata
        "match_mode": match_mode,
        "price_diff_usd": float(picks.get("price_diff_usd", float("nan"))),
        "price_diff_pct": float(picks.get("price_diff_pct", float("nan"))),
        "delta_diff_call": float(picks.get("delta_diff_call", float("nan"))),
        "delta_diff_put": float(picks.get("delta_diff_put", float("nan"))),
        "joint_picker_tolerance_delta": float(joint_delta_tol),
        "joint_picker_tolerance_price_pct": float(joint_price_tol_pct),
    }
    for c in M3_CONTEXT_COLS:
        v = m3_row.get(c) if isinstance(m3_row, dict) else None
        if v is None or (isinstance(v, float) and pd.isna(v)):
            trade_row[f"ctx_{c}"] = None if not isinstance(v, (int, float)) else float("nan")
        else:
            trade_row[f"ctx_{c}"] = v

    return trade_row, path_rows


def _process_friday_expiry(friday: date, expiry: date,
                           cost_cfg: dict,
                           conn: duckdb.DuckDBPyConnection,
                           joint_delta_tol: float,
                           joint_price_tol_pct: float
                           ) -> tuple[list[dict], list[dict]]:
    expiry_iso = expiry.isoformat()
    expiry_unix = expiry_dt_unix(expiry)

    win_start = entry_ts_for_friday(friday, ENTRY_HOURS_IST[0])
    win_end = sat_exit_ts_for_friday(friday)
    if expiry_unix <= win_start + 60:
        return [], []

    chain = load_chain_for_expiry(conn, expiry, win_start, win_end)
    if chain.empty:
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

    for hour_ist in ENTRY_HOURS_IST:
        entry_ts = entry_ts_for_friday(friday, hour_ist)
        for td in TARGET_DELTAS:
            try:
                result = build_trade(
                    friday, hour_ist, expiry, td,
                    entry_ts, win_end,
                    spot_series, chain_5m, atm_iv_series,
                    conn, cost_cfg,
                    joint_delta_tol, joint_price_tol_pct,
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


def _atomic_write_trades(df: pd.DataFrame, path: str) -> None:
    tmp = path + ".tmp"
    df.to_parquet(tmp, compression="zstd", index=False)
    os.replace(tmp, path)


def _append_safe_write_trades(new_trades: list[dict], out_path: str,
                              run_fridays: set[str]) -> int:
    """Idempotent append:
      1. Load existing parquet if present.
      2. Drop existing rows whose friday_date_ist is in run_fridays.
      3. Concat new trade rows.
      4. Atomic .tmp → rename.
    Returns total row count after write.
    """
    new_df = pd.DataFrame(new_trades) if new_trades else pd.DataFrame()
    if os.path.exists(out_path):
        existing = pd.read_parquet(out_path)
        if not existing.empty and "friday_date_ist" in existing.columns:
            existing = existing[~existing["friday_date_ist"].astype(str).isin(run_fridays)]
        if new_df.empty:
            combined = existing
        elif existing.empty:
            combined = new_df
        else:
            combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    if combined.empty:
        log.warning("Append write: combined frame empty; skipping write.")
        return 0
    _atomic_write_trades(combined, out_path)
    return int(len(combined))


def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    log.info("M7 joint (delta+price) backtester starting")
    log.info(f"  out trades  = {args.out_trades}")
    log.info(f"  out paths   = {args.out_paths}")
    log.info(f"  target deltas = {TARGET_DELTAS}")
    log.info(f"  qty lots    = {QTY_LOTS}")
    log.info(f"  joint_delta_tol     = {args.joint_delta_tol}")
    log.info(f"  joint_price_tol_pct = {args.joint_price_tol_pct}")
    log.info(f"  append      = {args.append}")
    log.info("─" * 60)

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
    os.makedirs(os.path.dirname(args.out_trades), exist_ok=True)
    os.makedirs(args.out_paths, exist_ok=True)

    all_trades: list[dict] = []
    run_fridays: set[str] = set()

    for fi, friday in enumerate(fridays, 1):
        run_fridays.add(friday.isoformat())
        conn = duckdb.connect()
        fri_start = entry_ts_for_friday(friday, ENTRY_HOURS_IST[0])
        fri_end = sat_exit_ts_for_friday(friday)
        fri_expiries = [e for e in expiries_filt
                        if expiry_dt_unix(e) > fri_start + 60
                        and expiry_dt_unix(e) < fri_end + 90 * 86400]

        friday_paths: list[dict] = []
        friday_trades: list[dict] = []

        for ei, expiry in enumerate(fri_expiries, 1):
            try:
                trades, paths = _process_friday_expiry(
                    friday, expiry, cost_cfg, conn,
                    args.joint_delta_tol, args.joint_price_tol_pct,
                )
            except Exception as e:
                log.exception(f"  friday={friday} expiry={expiry} failed: {e}")
                continue
            friday_trades.extend(trades)
            friday_paths.extend(paths)

        if friday_paths:
            paths_df = pd.DataFrame(friday_paths)
            part_dir = os.path.join(args.out_paths, f"friday_date={friday.isoformat()}")
            os.makedirs(part_dir, exist_ok=True)
            part_file = os.path.join(part_dir, "part.parquet")
            tmp = part_file + ".tmp"
            paths_df.to_parquet(tmp, compression="zstd", index=False)
            os.replace(tmp, part_file)

        all_trades.extend(friday_trades)
        n_joint = sum(1 for t in friday_trades if t.get("match_mode") == "joint")
        n_fall = sum(1 for t in friday_trades if t.get("match_mode") == "delta_fallback")
        log.info(f"  [{fi}/{len(fridays)}] {friday}: {len(friday_trades)} trades "
                 f"(joint={n_joint}, fallback={n_fall}), "
                 f"{len(friday_paths)} path rows")
        conn.close()

    if not all_trades:
        log.error("No trades produced; aborting trades-parquet write.")
        return

    if args.append:
        total = _append_safe_write_trades(all_trades, args.out_trades, run_fridays)
        log.info(f"  appended: total rows in parquet = {total:,}")
    else:
        df = pd.DataFrame(all_trades)
        _atomic_write_trades(df, args.out_trades)
        log.info(f"  overwrote: {len(df):,} rows → {args.out_trades}")

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"M7 joint done in {elapsed:.1f}s")
    log.info(f"  trades → {args.out_trades}")
    log.info(f"  paths partitioned by friday under: {args.out_paths}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--since", type=str, default=None)
    p.add_argument("--through", type=str, default=None)
    p.add_argument("--max-fridays", type=int, default=None)
    p.add_argument("--max-expiries", type=int, default=None)
    p.add_argument("--out-trades", type=str, default=TRADES_OUT)
    p.add_argument("--out-paths", type=str, default=PATHS_OUT_DIR)
    p.add_argument("--append", action="store_true",
                   help="Append-safe: drop rows whose friday_date is in the run range "
                        "and concat new rows, instead of overwriting.")
    p.add_argument("--joint-delta-tol", type=float, default=JOINT_DELTA_TOL,
                   help=f"Delta-window tolerance for joint picker (default {JOINT_DELTA_TOL}).")
    p.add_argument("--joint-price-tol-pct", type=float, default=JOINT_PRICE_TOL_PCT,
                   help=f"Max |Δprice|/mean_mark for joint accept (default {JOINT_PRICE_TOL_PCT}).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"M7 joint failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
