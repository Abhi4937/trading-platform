"""
M4 — Friday-overnight short-strangle batch backtester.

Generates a per-trade outcome dataset for use by M5 v2
(`backfill_attribution.py`) which adds pattern_winrate, z_winners_mean/std,
and expectancy stats to the calibration buckets.

Playbook (locked):
  - Entry: every Friday at 23:00 IST.
  - Exit:  Saturday at 10:00 IST (~11h overnight).
  - SL:    100% per leg (mark ≥ 2× entry mark for SELL leg → exit ALL legs).
  - Strategy: short strangle (SELL CE + SELL PE).
  - Quantity: 100 lots per leg.
  - Deltas:  0.05 / 0.10 / 0.15 / 0.25 / 0.30 / 0.50 (matches v1 calibration).
  - Expiries: every live expiry whose chain covers [Fri 23:00, Sat 10:00].
  - Costs:   slippage + brokerage applied (per existing model).
  - Margin:  tracked per trade via 29-scenario portfolio stress.
  - Win:     net_pnl_usd > 0.

Reads:
  /home/abhis/btc-data/data/options/expiry=*/strike=*/{CE,PE}.parquet
  /home/abhis/btc-data/derived/full_enriched_5m.parquet
  /home/abhis/btc-data/derived/calibration.parquet (only for bucket labelling alignment)

Writes:
  /home/abhis/btc-data/derived/m4_trades.parquet
  /home/abhis/btc-data/derived/m4_paths.parquet

Run:
    python -m app.analytics.m4_batch_backtester                        # full run
    python -m app.analytics.m4_batch_backtester --since 2026-04-01     # subset
    python -m app.analytics.m4_batch_backtester --max-fridays 2 --max-expiries 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import sys
import time as _time
from datetime import date, datetime, timedelta, timezone

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
    _snapshot_strike_marks,
    _spot_label,
)
from app.analytics.enrich_options import (
    DERIVED_DIR,
    expiry_dt_unix,
    list_expiries,
    load_chain_for_expiry,
)
from app.services.costs import CONTRACT_VALUE
from app.services.strangle_analytics import get_market_context
from app.services.trade_simulator import (
    LegSlSpec,
    PathSnapshot,
    TradeLegSpec,
    TradeResult,
    simulate_trade_path,
)

# ── Constants ────────────────────────────────────────────────────────────────

FULL_ENRICHED_5M_PATH = os.path.join(DERIVED_DIR, "full_enriched_5m.parquet")
TRADES_OUT_PATH = os.path.join(DERIVED_DIR, "m4_trades.parquet")
PATHS_OUT_PATH = os.path.join(DERIVED_DIR, "m4_paths.parquet")

TARGET_DELTAS = (0.05, 0.10, 0.15, 0.25, 0.30, 0.50)
QTY_LOTS = 100

# Friday 23:00 IST → 17:30 UTC (IST is UTC+5:30)
FRI_HOUR_UTC = 17
FRI_MIN_UTC = 30
# Saturday 10:00 IST → 04:30 UTC (next day)
EXIT_OFFSET_S = 11 * 3600  # 11 hours after entry

# Default cost config — matches the per-job backtester's standard settings
DEFAULT_COST_CFG = {
    "slippage": {"enabled": True, "mode": "smart", "flat_value": 5.0, "mult": 1.0},
    "brokerage": {"enabled": True, "rate": "offer", "referral": False},
}

# Default leg SL: 100% per leg
DEFAULT_LEG_SL_PCT = 100.0

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ── Friday timestamp grid ────────────────────────────────────────────────────

def friday_2300_ist_grid(start_unix: int, end_unix: int) -> list[int]:
    """All Friday 23:00 IST (= Fri 17:30 UTC) timestamps in [start, end]."""
    out: list[int] = []
    # Walk day by day from the first Friday >= start to the last Friday <= end
    start_dt = datetime.fromtimestamp(start_unix, tz=timezone.utc)
    cur = start_dt.replace(hour=FRI_HOUR_UTC, minute=FRI_MIN_UTC,
                            second=0, microsecond=0)
    # Advance to the next Friday (weekday 4 = Friday in Python)
    while cur.weekday() != 4:
        cur += timedelta(days=1)
    while int(cur.timestamp()) <= end_unix:
        ts = int(cur.timestamp())
        if ts >= start_unix:
            out.append(ts)
        cur += timedelta(days=7)
    return out


# ── M3 context cache (single-process) ────────────────────────────────────────

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
    snap_ts = ts - (ts % 300)  # 5m-aligned
    if snap_ts in m3.index:
        return m3.loc[snap_ts].to_dict()
    # Fallback: searchsorted for at-or-before
    pos = m3.index.searchsorted(snap_ts, side="right") - 1
    if pos < 0:
        return None
    return m3.iloc[pos].to_dict()


# ── Per-trade row builder ────────────────────────────────────────────────────

def _trade_id(entry_ts: int, expiry_iso: str, target_delta: float) -> int:
    """Stable 64-bit hash of (entry, expiry, delta)."""
    s = f"{entry_ts}|{expiry_iso}|{target_delta:.4f}"
    h = hashlib.sha256(s.encode()).digest()
    # First 8 bytes as unsigned 64-bit
    return int.from_bytes(h[:8], "big")


def _bucket_labels(dte: float, spot: float, target_delta: float, ivp: float) -> dict:
    return {
        "dte_bucket":   _label_for_range(DTE_BUCKETS, dte),
        "spot_bucket":  _spot_label(spot),
        "delta_target": _delta_label(target_delta),
        "ivp_bucket":   _label_for_range(IVP_BUCKETS, ivp),
    }


def _entry_filter_flags(m3_row: dict) -> dict:
    ivp = m3_row.get("ivp_atm_7d_90d") or 0.0
    ivp_4h = m3_row.get("ivp_4h") or 0.0
    rv7 = m3_row.get("rv_7d") or 0.0
    atm7 = m3_row.get("atm_iv_7d") or 0.0
    iv_rv_spread_pos = atm7 > (rv7 / 100.0) if rv7 else False
    adx = m3_row.get("adx_14_4h") or 0.0
    return {
        "flt_ivp_gt50":           ivp > 50,
        "flt_iv_rv_spread_pos":   iv_rv_spread_pos,
        "flt_adx_lt30":           adx < 30,
        "flt_dte_5_14":           False,    # placeholder; set per-trade
    }


def _dominant_greek(snapshots: list[PathSnapshot] | None,
                    spot_at_entry: float) -> str | None:
    """argmax over the trade's life of |theta·T|, |vega·dIV|, |gamma·dS²/2|.

    Approximation using snapshot-to-snapshot deltas.
    """
    if not snapshots or len(snapshots) < 2:
        return None
    cum = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    for prev, cur in zip(snapshots, snapshots[1:]):
        dT = (cur.ts - prev.ts) / 86400.0
        dS = cur.spot - prev.spot
        # Approximate position-level Greeks from leg deltas (gamma/vega/theta
        # not stored in PathSnapshot — use rough scaling for ranking only)
        pos_delta = sum(prev.leg_deltas)
        # Simple heuristic ranking using snapshot deltas + dS, dT
        cum["delta"] += abs(pos_delta * dS)
        cum["gamma"] += abs(0.5 * dS * dS / max(spot_at_entry, 1e-6))
        cum["theta"] += abs(dT)
    return max(cum, key=cum.get)


def _trade_row(result: TradeResult, expiry_iso: str, target_delta: float,
               m3_row: dict, call_strike: int, put_strike: int,
               call_iv: float, put_iv: float,
               call_delta: float, put_delta: float) -> dict:
    """Map a TradeResult to the m4_trades.parquet schema (one row)."""
    entry_ts = result.entry_ts
    exp_unix = expiry_dt_unix(date.fromisoformat(expiry_iso))
    dte_days = (exp_unix - entry_ts) / 86400.0
    spot_in = result.spot_at_entry

    # Marks are USD per contract; CONTRACT_VALUE = 0.001 BTC contract size.
    # Credit_usd = sum of leg marks (USD/contract) × number of contracts × CV.
    # Credit_pct = sum of leg marks / spot — the standard "% of spot" richness
    # convention, used by calibration buckets.
    total_credit = sum(result.entry_marks)
    credit_usd = total_credit * QTY_LOTS * CONTRACT_VALUE
    credit_pct = total_credit / spot_in if spot_in > 0 else float("nan")
    credit_pct_normalized = credit_pct / math.sqrt(max(dte_days, 1e-6)) if dte_days > 0 else float("nan")

    margin = result.margin_used or float("nan")
    net_pnl_pct_credit = result.net_pnl / credit_usd if credit_usd > 0 else float("nan")
    net_pnl_pct_margin = result.net_pnl / margin if margin and margin > 0 else float("nan")

    breaching_leg = None
    if result.breaching_leg_idx is not None:
        breaching_leg = "CE" if result.breaching_leg_idx == 0 else "PE"

    ivp = m3_row.get("ivp_atm_7d_90d") or float("nan")
    bucket = _bucket_labels(dte_days, spot_in, target_delta, ivp)

    flags = _entry_filter_flags(m3_row)
    flags["flt_dte_5_14"] = (5.0 <= dte_days <= 14.0)

    ent_dt = datetime.fromtimestamp(entry_ts, tz=timezone.utc) + timedelta(hours=5, minutes=30)
    entry_date_ist = ent_dt.strftime("%Y-%m-%d")

    row = {
        "trade_id": _trade_id(entry_ts, expiry_iso, target_delta),
        "entry_ts": int(entry_ts),
        "entry_date_ist": entry_date_ist,
        "expiry_date": expiry_iso,
        "expiry_unix": int(exp_unix),
        "dte_days": float(dte_days),
        "target_delta": float(target_delta),
        "call_strike": int(call_strike),
        "put_strike":  int(put_strike),
        "spot_at_entry": float(spot_in),
        "spot_at_exit": float(result.spot_at_exit),
        "call_entry_mark": float(result.entry_marks[0]),
        "put_entry_mark":  float(result.entry_marks[1]),
        "call_entry_iv":  float(call_iv),
        "put_entry_iv":   float(put_iv),
        "call_entry_delta": float(call_delta),
        "put_entry_delta":  float(put_delta),
        "total_credit": float(total_credit),
        "credit_usd": float(credit_usd),
        "credit_pct": float(credit_pct),
        "credit_pct_normalized": float(credit_pct_normalized),
        "exit_ts": int(result.exit_ts),
        "exit_reason": result.exit_reason,
        "breaching_leg": breaching_leg,
        "call_exit_mark": float(result.exit_marks[0]),
        "put_exit_mark":  float(result.exit_marks[1]),
        "gross_pnl_usd": float(result.gross_pnl),
        "slippage_usd":  float(result.entry_slip + result.exit_slip),
        "brokerage_usd": float(result.entry_brk + result.exit_brk),
        "net_pnl_usd":   float(result.net_pnl),
        "net_pnl_pct_credit": float(net_pnl_pct_credit),
        "net_pnl_pct_margin": float(net_pnl_pct_margin),
        "max_mtm_usd": float(result.max_mtm),
        "max_mtm_ts":  int(result.max_mtm_ts),
        "min_mtm_usd": float(result.min_mtm),
        "min_mtm_ts":  int(result.min_mtm_ts),
        "margin_used_usd": float(margin) if margin and not math.isnan(margin) else float("nan"),
        "outcome": "win" if result.net_pnl > 0 else "loss",
        "schema_version": 1,
    }
    # M3 context (frozen at entry) — prefix every col with `ctx_`
    for c in M3_CONTEXT_COLS:
        v = m3_row.get(c)
        row[f"ctx_{c}"] = v if v is not None else (float("nan") if isinstance(v, (int, float)) else None)
    # Bucket labels
    row.update(bucket)
    # Filter flags
    row.update(flags)
    # Dominant greek
    row["dominant_greek"] = _dominant_greek(result.snapshots, spot_in)
    return row


def _path_rows(snapshots: list[PathSnapshot] | None, trade_id: int) -> list[dict]:
    if not snapshots:
        return []
    out: list[dict] = []
    for snap in snapshots:
        out.append({
            "trade_id": trade_id,
            "ts": int(snap.ts),
            "spot": float(snap.spot),
            "call_mark": float(snap.leg_marks[0]),
            "put_mark":  float(snap.leg_marks[1]),
            "call_delta": float(snap.leg_deltas[0]),
            "put_delta":  float(snap.leg_deltas[1]),
            "call_iv": float(snap.leg_ivs[0]),
            "put_iv":  float(snap.leg_ivs[1]),
            "pnl_gross_usd": float(snap.pnl_gross_usd),
            "pnl_net_usd":   float(snap.pnl_net_usd),
        })
    return out


# ── Per-expiry processor (single shard) ──────────────────────────────────────

def _process_expiry(exp: date, fri_grid: list[int],
                    cost_cfg: dict, leg_sl_pct: float,
                    snapshot_cadence: int) -> tuple[list[dict], list[dict]]:
    """For one expiry: load chain once, iterate Friday entries × 6 deltas."""
    conn = duckdb.connect()
    exp_iso = exp.isoformat()
    exp_unix = expiry_dt_unix(exp)

    # Window of interest: from earliest Friday in range until the expiry itself
    win_start = max(min(fri_grid) - 7 * 86400, exp_unix - 90 * 86400)
    win_end = exp_unix - 60
    if win_end <= win_start:
        return [], []

    chain = load_chain_for_expiry(conn, exp, win_start, win_end)
    if chain.empty:
        return [], []
    # Snap to 5m-aligned timestamps (M2's native compute granularity)
    chain = chain[chain["timestamp_unix"] % 300 == 0]
    if chain.empty:
        return [], []
    chain_by_ts = {int(ts): g for ts, g in chain.groupby("timestamp_unix")}

    trades: list[dict] = []
    paths: list[dict] = []
    sl_specs = [LegSlSpec("pct", leg_sl_pct), LegSlSpec("pct", leg_sl_pct)]

    for entry_ts in fri_grid:
        sat_10_ts = entry_ts + EXIT_OFFSET_S
        if sat_10_ts > exp_unix - 60:
            continue
        # Snap entry to 5m grid for chain lookup (chain is 5m-aligned)
        snap_ts = entry_ts - (entry_ts % 300)
        snap = chain_by_ts.get(snap_ts)
        if snap is None or snap.empty:
            continue

        m3_row = _m3_at_or_before(entry_ts)
        if m3_row is None:
            continue
        spot = float(m3_row.get("close") or 0.0)
        if spot <= 0:
            continue
        T = (exp_unix - entry_ts) / (365.0 * 86400.0)
        if T <= 0:
            continue

        picks = _snapshot_strike_marks(snap, spot, T, TARGET_DELTAS)
        for td in TARGET_DELTAS:
            p = picks.get(td)
            if p is None:
                continue
            cs, ps = p["call_strike"], p["put_strike"]
            cm, pm = p["call_mark"], p["put_mark"]
            ci, pi = p["call_iv"], p["put_iv"]
            cd, pd_ = p["call_delta"], p["put_delta"]
            if any(np.isnan(x) for x in (cs, ps, cm, pm)):
                continue
            if cm <= 0 or pm <= 0:
                continue

            legs = [
                TradeLegSpec(exp_iso, int(cs), "CE", "SELL", QTY_LOTS),
                TradeLegSpec(exp_iso, int(ps), "PE", "SELL", QTY_LOTS),
            ]
            try:
                result = simulate_trade_path(
                    legs, entry_ts, sat_10_ts,
                    leg_sl_configs=sl_specs,
                    cost_cfg=cost_cfg,
                    record_snapshots=True,
                    snapshot_cadence_s=snapshot_cadence,
                    margin_compute=True,
                )
            except Exception as e:
                log.warning(f"  sim failed exp={exp_iso} td={td} ts={entry_ts}: {e}")
                continue
            if result is None:
                continue

            row = _trade_row(result, exp_iso, td, m3_row,
                             int(cs), int(ps), float(ci), float(pi),
                             float(cd), float(pd_))
            trades.append(row)
            paths.extend(_path_rows(result.snapshots, row["trade_id"]))
    return trades, paths


# ── Main loop ─────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    t0 = _time.time()
    log.info("M4 batch backtester starting")
    log.info(f"  out dir              = {args.out_dir}")
    log.info(f"  target deltas        = {TARGET_DELTAS}")
    log.info(f"  qty lots             = {QTY_LOTS}")
    log.info(f"  leg SL pct           = {args.leg_sl_pct}")
    log.info(f"  snapshot cadence (s) = {args.snapshot_cadence}")
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

    fri_grid = friday_2300_ist_grid(t_min, t_max)
    if args.max_fridays:
        fri_grid = fri_grid[: args.max_fridays]
    log.info(f"Friday entries: {len(fri_grid)} "
             f"({datetime.fromtimestamp(fri_grid[0], tz=timezone.utc) if fri_grid else 'n/a'} … "
             f"{datetime.fromtimestamp(fri_grid[-1], tz=timezone.utc) if fri_grid else 'n/a'})")

    expiries = list_expiries()
    # Only process expiries that overlap with the friday grid
    fri_min, fri_max = (min(fri_grid), max(fri_grid)) if fri_grid else (0, 0)
    expiries = [e for e in expiries
                if fri_min < expiry_dt_unix(e) and expiry_dt_unix(e) < fri_max + 90 * 86400]
    if args.max_expiries:
        expiries = expiries[: args.max_expiries]
    log.info(f"Expiries to process: {len(expiries)}")

    cost_cfg = DEFAULT_COST_CFG
    all_trades: list[dict] = []
    all_paths: list[dict] = []
    for i, exp in enumerate(expiries, 1):
        try:
            trades, paths = _process_expiry(
                exp, fri_grid, cost_cfg, args.leg_sl_pct, args.snapshot_cadence,
            )
        except Exception as e:
            log.exception(f"  expiry {exp} failed: {e}")
            continue
        all_trades.extend(trades)
        all_paths.extend(paths)
        if i % 10 == 0 or i == len(expiries):
            log.info(f"  [{i}/{len(expiries)}] processed; trades={len(all_trades)} paths={len(all_paths)}")

    if not all_trades:
        log.error("No trades generated; aborting write.")
        return

    # Write output parquets atomically (.tmp + rename)
    os.makedirs(args.out_dir, exist_ok=True)
    trades_df = pd.DataFrame(all_trades)
    paths_df = pd.DataFrame(all_paths) if all_paths else pd.DataFrame()

    out_trades = os.path.join(args.out_dir, "m4_trades.parquet")
    out_paths  = os.path.join(args.out_dir, "m4_paths.parquet")
    tmp_t = out_trades + ".tmp"
    tmp_p = out_paths + ".tmp"
    trades_df.to_parquet(tmp_t, compression="snappy", index=False)
    os.replace(tmp_t, out_trades)
    if not paths_df.empty:
        paths_df.to_parquet(tmp_p, compression="snappy", index=False)
        os.replace(tmp_p, out_paths)

    elapsed = _time.time() - t0
    log.info("─" * 60)
    log.info(f"M4 done in {elapsed:.1f}s")
    log.info(f"  trades = {len(trades_df):,} → {out_trades}")
    if not paths_df.empty:
        log.info(f"  paths  = {len(paths_df):,} → {out_paths}")
    n_win = int((trades_df["outcome"] == "win").sum())
    n_sl  = int((trades_df["exit_reason"] == "LegSL").sum())
    log.info(f"  win rate = {n_win}/{len(trades_df)} = {100*n_win/len(trades_df):.1f}%")
    log.info(f"  SL hit rate = {n_sl}/{len(trades_df)} = {100*n_sl/len(trades_df):.1f}%")


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
    p.add_argument("--leg-sl-pct", type=float, default=DEFAULT_LEG_SL_PCT,
                   help=f"Per-leg SL percent. Default: {DEFAULT_LEG_SL_PCT}.")
    p.add_argument("--snapshot-cadence", type=int, default=3600,
                   help="Path snapshot cadence in seconds. Default: 3600 (hourly).")
    p.add_argument("--out-dir", type=str, default=DERIVED_DIR,
                   help=f"Output directory. Default: {DERIVED_DIR}.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(args)
        return 0
    except Exception as e:
        log.exception(f"M4 failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
